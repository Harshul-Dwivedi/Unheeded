"""Approval gate: plain code with final authority over every brief (docs/architecture.md, Eval 3).

No LLM. Each line of a brief is parsed into claims and every claim is checked against the `ntsb` graph with direct
read-only Cypher (agent_ro). One failed claim blocks the brief. Each verdict names the path it searched and whether
a weaker path (a mere mention, or a repeat outside the open window) exists.

  ids        every recommendation / report / crash / letter id exists
  link       a recommendation in a crash brief is a linked warning for that crash, or, if the line says it is not, it isn't
  mention    "mentioned" lines: a MENTIONED_IN path exists
  repeat     "reiterated/reclassified/mentioned in [report]": that edge exists
  status     "rated X since D" / "on D" / "on the crash date" / "currently X" / "closed in YYYY" match HAD_STATUS
  issued     "issued D" equals the issue date;  killed: "(N killed)" equals the crash's fatalities
  count      "N linked warnings / safety recommendations" equals the number of linked warnings
  prevent    "prevent" only with a negation ("does not mean ... would have been prevented")
  person     no individual named (title + Name, Name X. Name, common first name + Surname)
  empty      a brief with no checkable claim is blocked (except the not-found / not-understood replies)
Lines inside the labelled "Unverified proposals" section skip the link check, unless they still call something linked.
"""
import datetime
import re
from functools import lru_cache

from unheeded.agents import LETTER, NTSB_NUM, REPORT, SRID, ids_in
from unheeded.config import connect
from unheeded.loader import status_code

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE = re.compile(r"\b(\d{1,2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{4})\b"
                  r"|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (\d{1,2}), (\d{4})\b"
                  r"|\b(\d{4})-(\d{2})-(\d{2})\b|\b((?:19|20)\d{2}[01]\d[0-3]\d)\b", re.I)
NTSB_STATUS = re.compile(r"\b(Open|Closed)\s*[—–-]*\s*((?:Acceptable|Unacceptable|Await|Initial|Reconsidered|No Longer|"
                         r"Exceeds|Superseded)[A-Za-z /-]*?)(?=[.;,)\]]|\s+(?:by|since|on|in|and|until|from)\b|$)", re.I)
PARAPHRASE = re.compile(r"\b(?:rated|classified|status(?: is)?|is now|was|is|remains?|currently)\s+(open|closed|unacceptable|"
                        r"acceptable)\b", re.I)
NUMBERS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                      "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
COUNT = re.compile(r"\b(\d{1,3}|" + "|".join(NUMBERS) + r")\s+(?:(?:earlier|older|previous|prior|open|still-open|linked|"
                   r"safety|NTSB)\s+){0,3}(?:warnings?|recommendations?)\b", re.I)  # adjectives only: "4 of the warnings" is no count claim
ROLES = (r"Mr|Mrs|Ms|Dr|Capt|Captain|Pilot|First Officer|Administrator|Chairman|Chairwoman|Chair|Vice Chairman|Board Member|"
         r"Secretary|Inspector|Controller|Mechanic|Senator|Representative|Congressman|Congresswoman|Governor|Director")
# a surname-shaped word: Smith, McDonald, O'Callaghan, Jean-Brice; ALL-CAPS only after Mr/Mrs/Ms/Dr ("MR. JAMES HENDERSON")
NAME_TOK = r"[A-Z](?:[a-z]+(?:[A-Z][a-z]+)?|['’][A-Z][a-z]+)(?:-[A-Z][a-z]+)?"
CAPS_NAME = r"(?:(?i:Mr|Mrs|Ms|Dr)\.?\s+(?:[A-Z]\.\s*)?[A-Z]{3,}(?:\s+[A-Z]{3,})?)"
TITLE = re.compile(r"\b(?:(?i:" + ROLES + r")\.?,?\s+(?:[A-Z]\.\s+)?(?=[A-Z][a-z']{2,}|[A-Z][a-z][A-Z])" + NAME_TOK + "|" + CAPS_NAME + ")")
MIDDLE_INITIAL = re.compile(r"\b" + NAME_TOK + r" [A-Z]\. " + NAME_TOK + r"\b")
TITLE_NAME = re.compile(r"\b((?i:" + ROLES + r")\.?,?)((?:\s+(?:[A-Z]\.\s*)?" + NAME_TOK + r"){1,3}|\s+(?:[A-Z]\.\s*)?[A-Z]{3,}(?:\s+[A-Z]{3,})?)")
# what follows a first-name pair that makes it a place or a firm, not a person ("Ronald Reagan Washington National Airport")
NOT_A_PERSON = re.compile(r"^(?:\s+[A-Z][a-z]+){0,3}\s+(?:Airport|International|Field|Airfield|Memorial|Regional|Systems|Aircraft|"
                          r"Aviation|Aerospace|Corporation|Company|Inc|Center|Bridge|County|Lake|River|Mountain)\b")
FIRST_NAMES = set("""james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony
mark donald steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary
nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory frank alexander raymond patrick jack
dennis jerry tyler aaron jose adam henry nathan douglas zachary peter kyle walter ethan jeremy harold keith christian
roger noah gerald carl terry sean austin arthur lawrence jesse dylan bryan joe jordan billy bruce albert willie gabriel
logan alan juan wayne roy ralph randy eugene vincent russell elijah louis bobby philip johnny mary patricia jennifer
linda elizabeth barbara susan jessica sarah karen nancy lisa betty margaret sandra ashley kimberly emily donna michelle
dorothy carol amanda melissa deborah stephanie rebecca sharon laura cynthia kathleen amy shirley angela helen anna brenda
pamela nicole emma samantha katherine christine debra rachel catherine carolyn janet ruth maria heather diane virginia
julie joyce victoria olivia kelly christina lauren joan evelyn judith megan cheryl andrea hannah martha jacqueline
frances gloria ann teresa kathryn sara janice jean alice madison doris abigail julia judy grace denise amber marilyn
beverly danielle theresa sophia marie diana brittany natalie isabella charlotte rose alexis kayla jane mike dan dave jim
tom bob bill steve chris matt tony nick ed ted rick rob ron don ken tim pete sam ben greg jeff andy phil pat kate liz sue
jen deb beth cathy kathy peggy sally cameron marion""".split())
CAP = re.compile(NAME_TOK)


def first_name_pairs(text):
    """Spans of '<common first name> <Capitalised word>', scanned with overlap ("From Billy Nolen" -> "Billy Nolen")."""
    toks = list(CAP.finditer(text))
    return [(a.start(), b.end()) for a, b in zip(toks, toks[1:])
            if text[a.end():b.start()] == " " and len(b.group(0)) >= 3 and a.group(0).lower() in FIRST_NAMES
            and not NOT_A_PERSON.match(text[b.end():])]


UNVERIFIED_HEAD = re.compile(r"^\W*unverified proposals?\b", re.I)  # the labelled section the Clerk prompt asks for
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)]|\[)")
AFFIRMS_LINK = re.compile(r"\blinked\b", re.I)
# "not linked", "is not a linked warning", "does not count": a status label like "Closed - No Longer Applicable" is no such claim
NOT_LINKED = re.compile(r"\b(?:not|never|no|isn't|doesn't|does not|is not)\b[^.;:]*?\b(?:linked|link|counts?)\b", re.I)
MENTION_HINT = re.compile(r"\bmention", re.I)
REPEAT_VERB = re.compile(r"\b(reiterated|repeated|reclassified|mentioned)\s+in\b", re.I)
NEGATION = re.compile(r"\b(not|never|no|doesn't|does not|cannot|isn't|is not)\b", re.I)


def to_int(m):
    """A DATE match -> yyyymmdd int, or None."""
    g = m.groups()
    try:
        if g[0]:
            return int(datetime.date(int(g[2]), MONTHS[g[1][:3].lower()], int(g[0])).strftime("%Y%m%d"))
        if g[3]:
            return int(datetime.date(int(g[5]), MONTHS[g[3][:3].lower()], int(g[4])).strftime("%Y%m%d"))
        if g[6]:
            return int(f"{g[6]}{g[7]}{g[8]}")
        return int(g[9])
    except ValueError:
        return None


def codes_for(phrase_open_closed, rest=None):
    """A status phrase -> the set of NTSB codes it can mean."""
    if rest is not None:
        c = status_code(phrase_open_closed, rest)
        return {c} if c else set()
    w = phrase_open_closed.lower()
    return {"open": {"OAA", "OUA", "OAR", "ORR", "OAAR"}, "closed": {"CAA", "CUA", "CAAA", "CR", "CNLA", "CS", "CUAS", "CAAS", "CEX", "CUAN"},
            "unacceptable": {"OUA", "CUA", "CUAS", "CUAN"}, "acceptable": {"OAA", "CAA", "OAAR", "CAAA", "CAAS"}}[w]


@lru_cache(maxsize=1)
def _graph():
    return connect("agent_ro").select_graph("ntsb")


class Facts:
    """Read-only lookups for one gate run, cached."""

    def __init__(self, g):
        self.g, self._rec, self._acc, self._linked, self.queries = g, {}, {}, {}, []

    def rows(self, q, **p):
        q = " ".join(q.split())
        if q not in self.queries:
            self.queries.append(q)  # distinct Cypher the gate ran, for the Step log
        return self.g.ro_query(q, p, timeout=1000).result_set

    def missing(self, ids):
        found = set()
        for q, key in (("MATCH (n:Recommendation) WHERE n.srid IN $ids RETURN n.srid", "rec"),
                       ("MATCH (n:Accident) WHERE n.ntsb_num IN $ids RETURN n.ntsb_num", "acc"),
                       ("MATCH (n:Report) WHERE n.display IN $ids RETURN n.display", "rep"),
                       ("MATCH (n:Letter) WHERE n.id IN $ids RETURN n.id", "let")):
            found |= {r[0] for r in self.rows(q, ids=sorted(ids))}
        return sorted(set(ids) - found)

    def rec(self, srid):
        if srid not in self._rec:
            r = self.rows("MATCH (r:Recommendation {srid: $s}) OPTIONAL MATCH (r)-[h:HAD_STATUS]->(st:Status) "
                          "RETURN r.status_code, r.issued_date, r.issued_from, r.closed_date, "
                          "collect({code: st.code, f: h.valid_from, t: h.valid_to, exact: h.exact})", s=srid)
            c, issued, how, closed, segs = r[0] if r else (None, None, None, None, [])
            self._rec[srid] = {"code": c, "issued": issued, "issued_from": how, "closed": closed,
                               "segments": sorted((x for x in segs if x["code"]), key=lambda x: x["f"])}
        return self._rec[srid]

    def status_on(self, srid, d):
        return next((x for x in self.rec(srid)["segments"] if x["f"] <= d and (x["t"] is None or x["t"] > d)), None)

    def acc(self, n):
        if n not in self._acc:
            r = self.rows("MATCH (a:Accident {ntsb_num: $n}) RETURN a.event_date, a.fatal, a.city", n=n)
            self._acc[n] = dict(zip(("date", "fatal", "city"), r[0])) if r else None
        return self._acc[n]

    def linked(self, n):
        if n not in self._linked:
            from unheeded.queries import FIND_OPEN_WARNINGS
            self._linked[n] = {r[0] for r in self.rows(FIND_OPEN_WARNINGS, id=n)}
        return self._linked[n]

    def why_not_linked(self, srid, n):
        """The weaker paths, for the verdict: mention only / repeated outside the open window / nothing."""
        rep = self.rows("MATCH (r:Recommendation {srid: $s})-[e:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident {ntsb_num: $n}) "
                        "RETURN r.issued_date, a.event_date, r.closed_date, (r)-[:ISSUED_FROM]->(a) LIMIT 1", s=srid, n=n)
        if rep:
            issued, event, closed, own = rep[0]
            if own:
                return "repeated only in the report on its own origin crash", "REITERATED_IN|RECLASSIFIED_IN (own origin)"
            if issued >= event:
                return f"issued after the crash (issued {issued}, crash {event})", "REITERATED_IN|RECLASSIFIED_IN (issued after)"
            return f"closed before the crash (closed {closed}, crash {event})", "REITERATED_IN|RECLASSIFIED_IN (closed before)"
        if self.rows("MATCH (:Recommendation {srid: $s})-[:MENTIONED_IN]->(:Report)-[:ABOUT]->(:Accident {ntsb_num: $n}) RETURN 1 LIMIT 1", s=srid, n=n):
            return "only mentioned in this crash's report (a mention is not a link)", "MENTIONED_IN"
        return "no path between them in the graph", None

    def edge(self, srid, report, kinds, crash=None):
        q = (f"MATCH (:Recommendation {{srid: $s}})-[:{'|'.join(kinds)}]->(p:Report) WHERE p.display = $r "
             + ("MATCH (p)-[:ABOUT]->(:Accident {ntsb_num: $c}) " if crash else "") + "RETURN 1 LIMIT 1")
        return bool(self.rows(q, s=srid, r=report, c=crash))


def _block(out, line, check, reason, searched=None, weaker=None):
    out["blocked"].append({"line": line.strip(), "check": check, "reason": reason, "path_searched": searched,
                           "weaker_path": weaker})


def check(text, subject=None, g=None, require_claims=True):
    """Verdict for a brief. subject: the crash (NTSB number) or recommendation (srid) the brief is about.
    require_claims: a brief with no checkable claim is blocked (False only for the not-found / not-understood replies)."""
    f = Facts(g or _graph())
    out = {"passed": False, "blocked": [], "warnings": [], "claims_checked": 0}
    subject_crash = subject if subject and NTSB_NUM.fullmatch(subject) else None
    subject_rec = subject if subject and SRID.fullmatch(subject) else None
    for m in f.missing(ids_in(text) | ({subject} if subject else set())):
        _block(out, m, "ids", f"{m} is not in the graph")
    subject_date = (f.acc(subject_crash) or {}).get("date") if subject_crash else None
    in_unverified = False
    for line in (x for x in text.splitlines() if x.strip()):
        # Only lines inside the labelled "Unverified proposals" section skip the link check, and not if they still say "linked"
        in_unverified = bool(UNVERIFIED_HEAD.search(line)) or (in_unverified and bool(BULLET.match(line)))
        srids = sorted({f"A-{y}-{int(n):03d}" for y, n in SRID.findall(line)}) or ([subject_rec] if subject_rec else [])
        crashes = sorted(set(NTSB_NUM.findall(line)))
        reports = sorted(set(REPORT.findall(line)))
        negated = bool(NOT_LINKED.search(line))
        unverified = in_unverified and not (AFFIRMS_LINK.search(line) and not negated)
        mention = bool(MENTION_HINT.search(line))
        target = crashes or ([subject_crash] if subject_crash else [])
        if re.search(r"\bprevent", line, re.I) and not NEGATION.search(line):
            _block(out, line, "prevent", "claims or implies prevention; 'linked' never means the crash would have been prevented")
        named = [m.group(0) for pat in (TITLE, MIDDLE_INITIAL) for m in pat.finditer(line)]
        named += [line[a:b] for a, b in first_name_pairs(line)]
        for n in named:
            _block(out, line, "person", f"names an individual ('{n}')")
        if re.search(r"\b(19|20)\d{6}\b", line):
            out["warnings"].append({"line": line.strip(), "reason": "raw yyyymmdd date; write it as e.g. 6 Feb 2025"})
        verb = REPEAT_VERB.search(line)
        for s in srids:
            if f.missing([s]):
                continue
            if verb and reports:  # "reiterated / reclassified / mentioned in [report] ([crash])"
                kinds = {"reiterated": ["REITERATED_IN"], "repeated": ["REITERATED_IN", "RECLASSIFIED_IN"],
                         "reclassified": ["RECLASSIFIED_IN"], "mentioned": ["MENTIONED_IN"]}[verb.group(1).lower()]
                for rep in reports:
                    out["claims_checked"] += 1
                    crash = crashes[0] if len(crashes) == 1 else None
                    if not f.edge(s, rep, kinds, crash):
                        _block(out, line, "repeat", f"no {'|'.join(kinds)} edge from {s} to {rep}" + (f" about {crash}" if crash else ""),
                               f"Recommendation-[{'|'.join(kinds)}]->Report" + ("-[ABOUT]->Accident" if crash else ""))
            elif target and not unverified and not subject_rec:  # a recommendation in a crash brief = a link claim
                for c in target:
                    out["claims_checked"] += 1
                    if negated:  # "only mentioned, not linked" / "issued after the crash, so it is not a linked warning"
                        if s in f.linked(c):
                            _block(out, line, "link", f"{s} IS a linked warning for {c}; the line says it is not")
                    elif mention:
                        if s not in f.linked(c) and f.why_not_linked(s, c)[1] != "MENTIONED_IN":
                            _block(out, line, "mention", f"no MENTIONED_IN path from {s} to {c}", "MENTIONED_IN")
                    elif s not in f.linked(c):
                        reason, weaker = f.why_not_linked(s, c)
                        _block(out, line, "link", f"{s} is not a linked warning for {c}: {reason}",
                               "Recommendation-[REITERATED_IN|RECLASSIFIED_IN]->Report-[ABOUT]->Accident, issued before, open on the crash date",
                               weaker)
        if srids:
            _status_claims(f, out, line, srids, subject_date)
        _number_claims(f, out, line, srids, subject_crash, crashes, unverified)
    if require_claims and not out["claims_checked"]:
        _block(out, "(whole brief)", "empty", "no claim in this brief could be checked against the graph")
    out["passed"] = not out["blocked"]
    out["queries"] = f.queries
    return out


def _clauses(line):
    return [c for c in re.split(r"[.;]\s+|\.$", line) if c.strip()]


def _status_claims(f, out, line, srids, subject_date):
    for clause in _clauses(line):
        phrases = [codes_for(m.group(1), m.group(2)) for m in NTSB_STATUS.finditer(clause)]
        phrases += [codes_for(m.group(1)) for m in PARAPHRASE.finditer(clause) if not NTSB_STATUS.search(clause[m.start():])]
        phrases = [p for p in phrases if p]
        if not phrases:
            continue
        clause_srids = sorted({f"A-{y}-{int(n):03d}" for y, n in SRID.findall(clause)}) or srids
        dates = [(m, to_int(m)) for m in DATE.finditer(clause)]
        since = next((d for m, d in dates if re.search(r"since\s*$", clause[:m.start()], re.I)), None)
        on = next((d for m, d in dates if re.search(r"\b(on|as of)\s*$", clause[:m.start()], re.I)), None)
        year = re.search(r"\bclosed\s+in\s+((?:19|20)\d{2})\b", clause, re.I)
        if on is None and re.search(r"on the crash date|on the day of the crash|at the time of the crash", clause, re.I):
            on = subject_date
        current = bool(re.search(r"\b(current(ly)?|now|today|still)\b", clause, re.I)) and not (since or on)
        for s in clause_srids:
            rec = f.rec(s)
            if not rec["code"]:
                continue
            for codes in phrases:
                out["claims_checked"] += 1
                if year:
                    if not (rec["closed"] and rec["closed"] // 10000 == int(year.group(1))):
                        _block(out, line, "status", f"{s} was not closed in {year.group(1)} (closed: {rec['closed']})", "Recommendation.closed_date")
                    continue
                if since:
                    seg = next((x for x in rec["segments"] if x["f"] == since and x["code"] in codes), None)
                    if not seg:
                        at = f.status_on(s, since)
                        _block(out, line, "status", f"{s} did not become {'/'.join(sorted(codes))} on {since} "
                                                    f"(status then: {at['code'] if at else 'not yet issued'})", "HAD_STATUS")
                if on:
                    at = f.status_on(s, on)
                    if not at or at["code"] not in codes:
                        _block(out, line, "status", f"{s} on {on} was {at['code'] if at else 'not yet issued'}, "
                                                    f"not {'/'.join(sorted(codes))}", "HAD_STATUS")
                if current or not (since or on):
                    if rec["code"] not in codes:
                        _block(out, line, "status", f"{s} is currently {rec['code']}, not {'/'.join(sorted(codes))}", "Recommendation.status_code")


def _number_claims(f, out, line, srids, subject, crashes, unverified=False):
    positions = [(m.start(), f"A-{m.group(1)}-{int(m.group(2)):03d}") for m in SRID.finditer(line)]
    for m in re.finditer(r"\bissued\s+(?:on\s+)?", line, re.I):
        d = DATE.match(line, m.end())
        if d and srids:
            claimed = to_int(d)
            s = ([x for pos, x in positions if pos < m.start()] or srids)[-1]  # the id just before "issued"
            rec = f.rec(s)
            out["claims_checked"] += 1
            if rec["issued"] and claimed != rec["issued"]:
                _block(out, line, "issued", f"{s} was issued {rec['issued']} ({rec['issued_from']}), not {claimed}", "Recommendation.issued_date")
    crash = (crashes or [subject])[0] if (crashes or subject) else None
    if crash and (k := re.search(r"\((\d+) killed\)", line)) and f.acc(crash):
        out["claims_checked"] += 1
        if int(k.group(1)) != (f.acc(crash)["fatal"] or 0):
            _block(out, line, "killed", f"{crash}: {f.acc(crash)['fatal']} killed, not {k.group(1)}", "Accident.fatal")
    if subject and not unverified:
        for m in COUNT.finditer(line):
            n = int(m.group(1)) if m.group(1).isdigit() else NUMBERS[m.group(1).lower()]
            if re.search(r"later reports|reports", line[m.end():m.end() + 20], re.I):
                continue
            out["claims_checked"] += 1
            if n != len(f.linked(subject)):
                _block(out, line, "count", f"{subject} has {len(f.linked(subject))} linked warnings, not {n}", "linked-warning query")


def scrub_names(text):
    """For display of official letters: drop the "From <name>, <title>:" header and any pattern the person check would
    block. Briefs are never scrubbed; the gate blocks them instead, so a person sees and fixes the sentence."""
    if not text:
        return text
    head = re.match(r"^\s*-?\s*From\s+([^:\n]{2,300}):\s*", text)  # "From <names, titles, orgs>:", whatever its shape,
    if head and not REPORT.search(head.group(1)) and not re.match(r"(?i)(the|an?|aviation|aircraft|accident|safety)\b", head.group(1)):
        text = text[head.end():]  # ...but not a source line such as "From Aviation Accident Report AAR-20-01, ...:"
    text = MIDDLE_INITIAL.sub("[name]", text)
    text = TITLE_NAME.sub(lambda m: m.group(1) + " [name]", text)
    for a, b in reversed(first_name_pairs(text)):
        text = text[:a] + "[name]" + text[b:]
    return text
