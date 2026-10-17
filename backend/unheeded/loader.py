"""Load the NTSB CAROL exports into the shared `ntsb` graph.

Rules come from docs/data.md and 04-graph-schema.md:
  - aviation recommendations only (Srid starts with "A-"); other modes ride along in the export via TopicMode
  - letters deduplicated per addressee on (date, type, first 120 chars)
  - report numbers normalised to [A-Z0-9] ("AIR-26-05" == "AIR2605"); display form kept
  - issue date = earliest of origin report/Board date, earliest letter, 31 Dec of the ID year (none before the ID year)
  - dates stored as yyyymmdd integers
  - safety studies are Report nodes, not Accidents; origins missing from the files become partial Accidents
  - multi-aircraft suffixes (CEN19MA141AB) resolve to the base investigation when the files hold it
  - never an REITERATED_IN / RECLASSIFIED_IN / MENTIONED_IN edge to a report about the rec's own origin

Run: python -m unheeded.loader [data_dir]
"""
import datetime
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from unheeded.config import connect  # noqa: F401  (re-exported: eval, enrich, tests import it from here)

GRAPH = "ntsb"
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
FILES = {
    "recs": "carol_recommendations_data.json",
    "inv": "carol_investigations_data.json",
    "pool": "carol_aviation_investigations_data.json",
}
NTSB = "National Transportation Safety Board"
RPT = re.compile(r"\b[A-Z]{2,4}-\d{2}-\d{2}\b")
EDGE_FOR = {
    "Recommendation Reiteration": "REITERATED_IN",
    "Report Reclassification": "RECLASSIFIED_IN",
    "Recommendation Mention": "MENTIONED_IN",  # discussed only; never a link
}
HUB_SHARE = 0.10  # family projection: drop probable-cause findings on more than this share of coded crashes
SUP = re.compile(r"supersede(s|d by)\b([^);]*)", re.I)
SRID = re.compile(r"\bA-(\d{2})-(\d{1,3})(?:\s+through\s+(?:A-\d{2})?-?(\d{1,3}))?")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def ymd(s):
    s = (s or "")[:10]
    return int(s.replace("-", "")) if s and not s.startswith("0001") else None


def report_key(raw):
    """Normalised report number from a CAROL field, or None for 'None', 'Foreign Report', 'Unavailable'."""
    m = RPT.search(str(raw or ""))
    if m:
        return norm(m.group(0)), m.group(0)
    k = norm(raw)
    if re.fullmatch(r"[A-Z]{2,4}\d{4,5}", k):  # investigation-file form, e.g. AIR2605
        return k, f"{k[:-4]}-{k[-4:-2]}-{k[-2:]}"
    return None, None


def is_study(ntsb_num):
    """Safety studies / special investigations: type code SS, SR or SP (DCA22SS002, DCA23SR001, DCA20SP001)."""
    return ntsb_num[5:7] in ("SS", "SR", "SP")


def base_id(ntsb_num, known):
    """Multi-aircraft events: Notations cite CEN19MA141AB where the files hold CEN19MA141."""
    b = re.sub(r"(?<=\d)[A-Z]+$", "", ntsb_num)
    return b if b != ntsb_num and b in known else ntsb_num


def srid3(year, num):
    return f"A-{year}-{int(num):03d}"


def superseded_pairs(srid, subject):
    """(old, new) pairs from '(Supersedes A-18-31)' / '(Superseded by A-93-8 through A-93-12)'."""
    out = []
    for m in SUP.finditer(subject or ""):
        ids = []
        for y, a, b in SRID.findall(m.group(2)):
            ids += [srid3(y, n) for n in range(int(a), int(b or a) + 1)]
        by = m.group(1).lower() != "s"
        out += [(srid, i) if by else (i, srid) for i in ids]
    return out


STATUS = re.compile(r"\b(OPEN|CLOSED)\s*[\u2014\u2013\-]+\s*([A-Z][A-Z /\-]{3,60}?)"
                    r"(?=[\u201d\"'.,;)]|\s+(?:on|and|pending|in|because|until|status)\b|$)", re.I)
LETTER_SRIDS = re.compile(r"\bA-(\d{2})-(\d{1,3})\b((?:\s*(?:,|and|through|to|&)\s*-\s*\d{1,3}\b)*)")


def status_code(open_closed, rest):
    """'Open', 'Unacceptable Response' -> 'OUA'. Covers the 142 spellings seen in NTSB letters, typos included."""
    o, r = open_closed.upper()[0], rest.upper()
    if "UNACC" in r:
        return "CUAN" if o == "C" and "NO RESPONSE" in r else "CUAS" if o == "C" and "SUPERSED" in r else o + "UA"
    if "ALTERNAT" in r:
        return "OAAR" if o == "O" else "CAAA"
    if "ACCEPT" in r:
        return "CAAS" if o == "C" and "SUPERSED" in r else o + "AA"
    for o2, word, c in (("O", "AWAIT", "OAR"), ("O", "INITIAL", "ORR"), ("C", "RECONSIDER", "CR"), ("C", "LONGER", "CNLA"),
                        ("C", "EXCEED", "CEX"), ("C", "SUPERSED", "CS")):
        if o == o2 and word in r:
            return c
    return None


def srids_in(text):
    """Rec ids in letter prose: 'A-16-35', 'A-16-034 through -036', 'A-16-34 and -35'."""
    out = set()
    for y, first, tail in LETTER_SRIDS.findall(text):
        prev = int(first)
        nums = [prev]
        for sep, n in re.findall(r"(through|to|,|and|&)\s*-\s*(\d{1,3})", tail):
            n = int(n)
            nums += range(prev + 1, n + 1) if sep in ("through", "to") else [n]
            prev = n
        out |= {f"A-{y}-{k:03d}" for k in nums}
    return out


def classified_as(srid, text):
    """Last status the letter assigns to `srid`. A sentence naming only other recs is skipped."""
    in_letter, last = srids_in(text), None
    for sentence in re.split(r"(?<=[.;:])\s+|(?<=[.;:][\u201d\"'])\s+", text):  # a period may sit inside the quote
        for m in STATUS.finditer(sentence):
            ids = srids_in(sentence[:m.start()]) or srids_in(sentence)
            if srid in ids or (not ids and in_letter <= {srid}):
                last = status_code(m.group(1), m.group(2)) or last
    return last


def next_day(d):
    return int((datetime.date(d // 10000, d // 100 % 100, d % 100) + datetime.timedelta(days=1)).strftime("%Y%m%d"))


# same-day letters: the Board's report reclassification wins over prose in other letters
TYPE_RANK = {"Report Reclassification": 2, "Official Correspondence": 1}


def status_history(issued, closed, current, events):
    """Segments {code, valid_from, valid_to, basis, letter, exact} from issue to now.

    events: (date, type, code, letter_id) parsed from NTSB letters. Starts at Open-Await Response on the issue date
    (NTSB's status for a rec with no classified response yet); closed recs end at DateClosed with the current code.
    """
    if closed and closed <= issued:  # closed on the day it was issued
        return [{"code": current, "valid_from": issued, "basis": "date_closed", "exact": True}]
    seg = [{"code": "OAR", "valid_from": issued, "basis": "issued_default", "exact": True}]
    seen = issued
    for d, t, code, lid in sorted(events, key=lambda e: (e[0], TYPE_RANK.get(e[1], 0))):
        if d < issued or (closed and d >= closed):
            continue
        seen = max(seen, d)
        last = seg[-1]
        if last["valid_from"] == d:  # same day: the higher-ranked letter overrides
            last.update(code=code, basis="letter", letter=lid)
            if len(seg) > 1 and seg[-2]["code"] == code:
                seg.pop()
        elif code != last["code"]:
            seg.append({"code": code, "valid_from": d, "basis": "letter", "letter": lid, "exact": True})
    if closed and seg[-1]["code"] != current:
        seg.append({"code": current, "valid_from": max(closed, issued), "basis": "date_closed", "exact": closed >= issued})
    elif not closed and seg[-1]["code"] != current:  # changed after the last letter we have; the day is unknown
        seg.append({"code": current, "valid_from": next_day(seen), "basis": "current_record", "exact": False})
    for a, b in zip(seg, seg[1:]):
        a["valid_to"] = b["valid_from"]
    return seg


def dedupe_letters(addressee):
    seen, out = set(), []
    for c in addressee.get("Correspondence") or []:
        k = (c.get("CorrespondenceDate"), c.get("CorrespondenceType"), (c.get("ResponseSummary") or "")[:120])
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


def clean(d):
    return {k: v for k, v in d.items() if v is not None}


def build(recs, inv, pool, recorded_at=None):
    """Pure transform: CAROL records -> {"nodes": {label: [props]}, "edges": {(type, sl, sk, dl, dk): [rows]}}."""
    recorded_at = recorded_at or int(time.strftime("%Y%m%d"))  # when this load learned the facts (bi-temporal)
    pool_ids = {r["NTSBNumber"] for r in pool if r.get("Mode") == "Aviation"}
    invs = {r["NTSBNumber"]: r for r in pool if r.get("Mode") == "Aviation"}
    invs.update({r["NTSBNumber"]: r for r in inv})
    recs = [r for r in recs if r["Srid"].startswith("A-")]

    accidents, reports, orgs, statuses = {}, {}, {}, {}
    findings, events, letters = {}, {}, []
    E = defaultdict(list)
    about = set()  # (report_num, ntsb_num)

    def add_report(key, display, **props):
        r = reports.setdefault(key, {"report_num": key, "display": display, "kind": (re.match(r"[A-Z]+", key) or [None])[0], "is_study": False})
        r.update(clean(props))
        return key

    def org(name, acronym):
        orgs.setdefault(name, {"name": name, "acronym": acronym or None})
        return name

    study_report = {}  # ntsb_num of a study -> its Report key
    for n, r in invs.items():
        key, disp = report_key(r.get("ReportNumber"))
        dates = dict(report_date=ymd(r.get("ReportDate")), board_date=ymd(r.get("BoardMeetingDate")))
        if r.get("IsStudy") or is_study(n):
            study_report[n] = add_report(key or norm(n), disp or n, is_study=True, ntsb_num=n, **dates)
            continue
        vs = r.get("Vehicles") or []
        accidents[n] = clean({
            "ntsb_num": n, "event_date": ymd(r.get("EventDateTimeUTC")), "city": r.get("City"),
            "state": r.get("StateOrRegion"), "country": r.get("Country"),
            "lat": float(r["Latitude"]) if r.get("Latitude") is not None else None,
            "lon": float(r["Longitude"]) if r.get("Longitude") is not None else None,
            "fatal": r.get("FatalInjuryCount") or 0, "injury_level": r.get("HighestInjuryLevel"),
            "probable_cause": r.get("ProbableCause"), "narrative": r.get("AnalysisNarrative"),
            "in_pool": n in pool_ids, "partial": False,
        })
        if key:
            add_report(key, disp, **dates)
            about.add((key, n))
        has, had = {}, {}
        for v in vs:
            for f in v.get("Findings") or []:
                code = f["FindingCode"]
                findings.setdefault(code, clean({"code": code, **{f"tier{i}": f.get(f"finding_Tier{i}Name") for i in range(1, 5)},
                                                 "modifier": f.get("finding_ModifierName")}))
                has[code] = has.get(code, False) or bool(f.get("InProbableCause"))
            for ev in v.get("Events") or []:
                eid = f'{ev.get("CICTTEventSOEGroup")}|{ev.get("CICTTPhaseSOEGroup")}'
                events.setdefault(eid, clean({"id": eid, "cictt_group": ev.get("CICTTEventSOEGroup"), "phase": ev.get("CICTTPhaseSOEGroup")}))
                had[eid] = had.get(eid, False) or bool(ev.get("IsDefiningEvent"))
        for eid, defining in had.items():
            E[("HAD_EVENT", "Accident", "ntsb_num", "EventType", "id")].append({"s": n, "d": eid, "p": {"defining": defining}})
        for code, in_pc in has.items():
            E[("HAS_FINDING", "Accident", "ntsb_num", "FindingCode", "code")].append({"s": n, "d": code, "p": {"in_probable_cause": in_pc}})

    # Family projection (enrich.families): probable-cause findings + each crash's defining event. Hub findings
    # (on > 10% of coded crashes, e.g. "Personnel issues / use of equipment / pilot", 19%) are left out: they glue
    # unrelated crashes into one giant family (46% -> 17% largest family, 129 -> 266 families; coverage stays 93%).
    pc_rows = [e for e in E[("HAS_FINDING", "Accident", "ntsb_num", "FindingCode", "code")] if e["p"]["in_probable_cause"]]
    coded = {e["s"] for k in (("HAS_FINDING", "Accident", "ntsb_num", "FindingCode", "code"), ("HAD_EVENT", "Accident", "ntsb_num", "EventType", "id"))
             for e in E[k]}
    per_code = {}
    for e in pc_rows:
        per_code[e["d"]] = per_code.get(e["d"], 0) + 1
    hubs = {c for c, n in per_code.items() if n > HUB_SHARE * len(coded)}
    E[("HAS_PC_FINDING", "Accident", "ntsb_num", "FindingCode", "code")] = [{"s": e["s"], "d": e["d"], "p": {}} for e in pc_rows if e["d"] not in hubs]
    E[("HAD_DEFINING_EVENT", "Accident", "ntsb_num", "EventType", "id")] = [
        {"s": e["s"], "d": e["d"], "p": {}} for e in E[("HAD_EVENT", "Accident", "ntsb_num", "EventType", "id")] if e["p"]["defining"]]

    # origins: one Notation per recommendation in this export
    origin_of = {}  # srid -> ("Accident", ntsb_num) | ("Report", report_num)
    for rec in recs:
        for no in rec["Notations"]:
            n = base_id(no["NtsbNum"], invs)
            key, disp = report_key(no.get("ReportNum"))
            if n in study_report:
                origin_of[rec["Srid"]] = ("Report", study_report[n])
            elif n in accidents:
                origin_of[rec["Srid"]] = ("Accident", n)
            elif no["AccidentDate"].startswith("0001") or is_study(n):  # safety study not in the files
                study_report[n] = add_report(key or norm(n), disp or n, is_study=True, ntsb_num=n)
                origin_of[rec["Srid"]] = ("Report", study_report[n])
            else:  # legacy / foreign origin only known from the Notation
                accidents[n] = clean({"ntsb_num": n, "event_date": ymd(no["AccidentDate"]), "city": no.get("City") or None,
                                      "state": no.get("State") or None, "in_pool": False, "partial": True})
                origin_of[rec["Srid"]] = ("Accident", n)
            if key and n in accidents:
                add_report(key, disp)
                about.add((key, n))

    about_of = defaultdict(set)
    for k, n in about:
        about_of[k].add(n)
        E[("ABOUT", "Report", "report_num", "Accident", "ntsb_num")].append({"s": k, "d": n, "p": {}})

    def issue_date(rec):
        """Earliest of origin report/Board date, earliest letter or DateClosed, 31 Dec of the ID year: each is an upper bound
        (early recs precede the report; no letter precedes the rec). Candidates before the ID year are bad data."""
        y = int(rec["Srid"].split("-")[1])
        y = 1900 + y if y >= 60 else 2000 + y
        cands = [(y * 10000 + 1231, 2, "id")]
        for no in rec["Notations"]:
            o = invs.get(base_id(no["NtsbNum"], invs))
            if o:
                cands += [(d, 0, "report") for d in (ymd(o.get("BoardMeetingDate")), ymd(o.get("ReportDate"))) if d]
        cands += [(ymd(c["CorrespondenceDate"]), 1, "letter") for a in rec["Addressees"] for c in a["Correspondence"]
                  if ymd(c.get("CorrespondenceDate"))]
        if ymd(rec.get("DateClosed")):
            cands.append((ymd(rec["DateClosed"]), 1, "letter"))  # the closing letter, even when the export lacks it
        d, _, how = min(c for c in cands if c[0] >= y * 10000 + 101)
        return d, how

    org(NTSB, "NTSB")
    statuses = {r["OverallStatus"]: {"code": r["OverallStatus"], "label": r["OpenClosed"]} for r in recs}
    rec_rows, srids, sup = [], {r["Srid"] for r in recs}, set()
    for rec in recs:
        s = rec["Srid"]
        issued, how = issue_date(rec)
        rec_rows.append(clean({
            "srid": s, "text": rec["Subject"], "status_code": rec["OverallStatus"], "status_label": rec["OpenClosed"],
            "is_open": rec["OverallStatus"].startswith("O"), "closed_date": ymd(rec.get("DateClosed")),
            "issued_date": issued, "issued_from": how, "priority": rec.get("Priority"),
            "most_wanted": bool(rec.get("MostWanted")), "times_reiterated": rec.get("TimesReiterated") or 0,
            "letter_url": rec.get("GreenSheet") or None,
        }))
        kind, okey = origin_of[s]
        E[("ISSUED_FROM", "Recommendation", "srid", kind, "ntsb_num" if kind == "Accident" else "report_num")].append({"s": s, "d": okey, "p": {}})
        own_accidents = {okey} if kind == "Accident" else set()
        own_reports = {okey} if kind == "Report" else set()
        sup.update(p for p in superseded_pairs(s, rec["Subject"]) if p[0] in srids and p[1] in srids and p[0] != p[1])

        ref = {}  # (edge type, report) -> {date, letters}
        classified = []  # status classifications parsed from NTSB letters
        for a in rec["Addressees"]:
            name = org(a.get("Name") or a.get("AddressAcronym"), a.get("AddressAcronym"))
            E[("ADDRESSED_TO", "Recommendation", "srid", "Organization", "name")].append(
                {"s": s, "d": name, "p": clean({"status": a.get("AddresseeStatus"), "status_label": a.get("OpenClosedAddressee")})})
            for c in dedupe_letters(a):
                lid = f"L{len(letters) + 1}"
                from_ntsb = bool(c.get("IsFromNtsb"))
                letters.append(clean({"id": lid, "date": ymd(c.get("CorrespondenceDate")), "type": c.get("CorrespondenceType") or None,
                                      "from_ntsb": from_ntsb, "summary": c.get("ResponseSummary") or None}))
                E[("ABOUT", "Letter", "id", "Recommendation", "srid")].append({"s": lid, "d": s, "p": {}})
                E[("FROM", "Letter", "id", "Organization", "name")].append({"s": lid, "d": NTSB if from_ntsb else name, "p": {}})
                E[("TO", "Letter", "id", "Organization", "name")].append({"s": lid, "d": name if from_ntsb else NTSB, "p": {}})
                d = ymd(c.get("CorrespondenceDate"))
                code = from_ntsb and d and classified_as(s, c.get("ResponseSummary") or "")
                if code:
                    classified.append((d, c.get("CorrespondenceType"), code, lid))
                t = EDGE_FOR.get(c.get("CorrespondenceType"))
                if not t:
                    continue
                for m in RPT.finditer(c.get("ResponseSummary") or ""):
                    k = norm(m.group(0))
                    if k in own_reports or about_of.get(k, set()) & own_accidents:
                        continue  # design rule 3: never link to the rec's own origin
                    add_report(k, m.group(0))
                    d = ymd(c.get("CorrespondenceDate"))
                    e = ref.setdefault((t, k), {"date": d, "letters": 0})
                    e["letters"] += 1
                    if d and (e["date"] is None or d < e["date"]):
                        e["date"] = d
        for (t, k), p in ref.items():
            E[(t, "Recommendation", "srid", "Report", "report_num")].append({"s": s, "d": k, "p": clean(p)})
        for h in status_history(issued, ymd(rec.get("DateClosed")), rec["OverallStatus"], classified):
            E[("HAD_STATUS", "Recommendation", "srid", "Status", "code")].append(
                {"s": s, "d": h.pop("code"), "p": clean({**h, "recorded_at": recorded_at})})

    for old, new in sorted(sup):
        E[("SUPERSEDED_BY", "Recommendation", "srid", "Recommendation", "srid")].append({"s": old, "d": new, "p": {}})

    return {
        "nodes": {
            "Recommendation": rec_rows, "Accident": list(accidents.values()), "Report": list(reports.values()),
            "Organization": list(orgs.values()), "Letter": letters, "Status": list(statuses.values()),
            "FindingCode": list(findings.values()), "EventType": list(events.values()),
        },
        "edges": dict(E),
    }


INDEXES = [
    ("Recommendation", "srid"), ("Accident", "ntsb_num"), ("Report", "report_num"), ("Organization", "name"),
    ("Organization", "acronym"), ("Letter", "id"), ("Letter", "date"), ("Status", "code"), ("FindingCode", "code"),
    ("EventType", "id"),
]


def batches(rows, n=2000):
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


def write(g, model):
    try:
        g.delete()
    except Exception:  # graph does not exist yet
        pass
    for label, prop in INDEXES:
        g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    g.query("CALL db.idx.fulltext.createNodeIndex('Recommendation', 'text')")
    g.query("CALL db.idx.fulltext.createNodeIndex('Accident', 'city', 'state')")  # resolve_accident by place
    for label, rows in model["nodes"].items():
        for b in batches(rows):
            g.query(f"UNWIND $rows AS row CREATE (n:{label}) SET n = row", {"rows": b})
    for (t, sl, sk, dl, dk), rows in model["edges"].items():
        for b in batches(rows):
            res = g.query(f"UNWIND $rows AS row MATCH (a:{sl} {{{sk}: row.s}}) MATCH (b:{dl} {{{dk}: row.d}}) "
                          f"CREATE (a)-[e:{t}]->(b) SET e = row.p, e.source = 'ntsb_official'", {"rows": b})
            if res.relationships_created != len(b):  # fail closed: an endpoint was missing
                raise RuntimeError(f"{t} {sl}->{dl}: {res.relationships_created}/{len(b)} edges created")


def read(data_dir):
    return [json.load(open(Path(data_dir) / FILES[k])) for k in ("recs", "inv", "pool")]


def main():
    t0 = time.time()
    model = build(*read(sys.argv[1] if len(sys.argv) > 1 else DATA_DIR))
    g = connect().select_graph(GRAPH)
    write(g, model)
    for label, rows in model["nodes"].items():
        print(f"{label:15} {len(rows):6}")
    for key, rows in sorted(model["edges"].items()):
        print(f"{key[0]:15} {key[1]}->{key[3]:14} {len(rows):6}")
    print(f"loaded in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
