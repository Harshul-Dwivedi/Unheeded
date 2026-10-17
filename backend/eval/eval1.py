"""Eval 1 — linked-warning recovery (docs/evals.md).

Pass when: every gold link is returned (recall 100%), nothing extra on gold crashes, 0/50 false positives on
G-none (US crashes only), 0 false positives on G-near (pairs that almost qualify: the recommendation is in the crash's
own report, but only mentioned, issued after the crash, closed before it, or issued from it), median
find_open_warnings < 20 ms, and the traps hold. G-near is the hard set: G-none crashes have no path at all. Writes eval/report_eval1.json; exits 1 on failure.
Run: python -m eval.eval1   (graph must be loaded first)
"""
import json
import statistics
import sys
import time
from pathlib import Path

from unheeded import queries as Q
from unheeded.loader import DATA_DIR, GRAPH, RPT, connect, norm, read

GOLD = DATA_DIR.parent / "derived" / "gold_links_strict_definition.json"
OUT = Path(__file__).parent / "report_eval1.json"


def g_none(recs, pool, n=50):
    """Fatal pool accidents with no cross-reference to any recommendation, chosen from the raw files (not the graph)."""
    origins = {no["NtsbNum"] for r in recs for no in r["Notations"]}
    text = " ".join(c.get("ResponseSummary") or "" for r in recs for a in r["Addressees"] for c in a["Correspondence"])
    cited = {norm(m) for m in RPT.findall(text)}
    us = lambda a: a.get("Country") == "USA" or (not a.get("Country") and a["NTSBNumber"][5:7] not in ("WA", "RA"))
    out = [a for a in pool
           if us(a) and (a.get("FatalInjuryCount") or 0) > 0 and a["NTSBNumber"] not in origins and a["NTSBNumber"] not in text
           and norm(a.get("ReportNumber")) not in cited]
    out.sort(key=lambda a: (-a["FatalInjuryCount"], a["NTSBNumber"]))
    return [a["NTSBNumber"] for a in out[:n]]


NEAR = """
MATCH (r:Recommendation)-[e:REITERATED_IN|RECLASSIFIED_IN|MENTIONED_IN]->(:Report)-[:ABOUT]->(a:Accident)
WHERE """ + Q.US + """
WITH r, a, collect(DISTINCT type(e)) AS kinds
RETURN r.srid, a.ntsb_num, CASE WHEN NOT 'REITERATED_IN' IN kinds AND NOT 'RECLASSIFIED_IN' IN kinds THEN 'mention only'
            WHEN size((r)-[:ISSUED_FROM]->(a)) > 0 THEN 'issued from the crash' WHEN r.issued_date >= a.event_date THEN 'issued after'
            WHEN r.closed_date IS NOT NULL AND r.closed_date <= a.event_date THEN 'closed before' ELSE 'qualifies' END
"""


def timed(g, q, params):
    t = time.perf_counter()
    res = g.ro_query(q, params)
    return res.result_set, (time.perf_counter() - t) * 1000, res.run_time_ms


def main():
    recs, _, pool = read(DATA_DIR)
    gold = json.load(open(GOLD))
    gold_pairs = {(i["ntsb_num"], w) for i in gold["items"] for w in i["warnings"]}
    g = connect().select_graph(GRAPH)

    found_all = {(row[0], s) for row in g.ro_query(Q.ALL_LINKED_CRASHES).result_set for s in row[4]}

    per_crash, wall, server = {}, [], []
    for n in sorted({c for c, _ in gold_pairs}):
        rows, w, s = timed(g, Q.FIND_OPEN_WARNINGS, {"id": n})
        per_crash[n] = [r[0] for r in rows]
        wall.append(w); server.append(s)
    found_crash = {(n, s) for n, ss in per_crash.items() for s in ss}

    negatives = g_none(recs, pool)
    fp = {}
    for n in negatives:
        rows, w, s = timed(g, Q.FIND_OPEN_WARNINGS, {"id": n})
        absent = g.ro_query(Q.PROVE_NO_LINKED_WARNING, {"id": n}).result_set
        wall.append(w); server.append(s)
        if rows or [r[:2] for r in absent] != [[n, True]]:
            fp[n] = [r[0] for r in rows]

    # G-near: every US pair with a path into the crash's report that the answer key (gold, built from the raw files)
    # does not hold. Its label says why it should not count; none may come back from the headline query.
    near = [(s, n, why) for s, n, why in g.ro_query(NEAR).result_set if (n, s) not in gold_pairs]
    near_fp = []
    for n in sorted({n for _, n, _ in near}):
        returned = {r[0] for r in g.ro_query(Q.FIND_OPEN_WARNINGS, {"id": n}).result_set}
        near_fp += [(s, n2, why) for s, n2, why in near if n2 == n and s in returned]
    near_kinds = {}
    for _, _, why in near:
        near_kinds[why] = near_kinds.get(why, 0) + 1

    one = lambda q, p=None: g.ro_query(q, p).result_set[0][0]
    traps = {
        "nome_mention_A-21-049_not_linked": "A-21-049" not in per_crash.get("ANC25MA018", []) and one(
            "MATCH (:Recommendation {srid:'A-21-049'})-[:MENTIONED_IN]->(:Report)-[:ABOUT]->(:Accident {ntsb_num:'ANC25MA018'}) RETURN count(*) > 0"),
        "togiak_not_linked_to_A-16-035": "A-16-035" not in per_crash.get("ANC17MA001", []),
        "no_self_links": one(
            "MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN|MENTIONED_IN]->(:Report)-[:ABOUT]->(a:Accident)<-[:ISSUED_FROM]-(r) "
            "RETURN count(*) = 0"),
        "no_self_links_study": one(
            "MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN|MENTIONED_IN]->(p:Report)<-[:ISSUED_FROM]-(r) RETURN count(*) = 0"),
    }

    report = {
        "gold": {"crashes": gold["crashes"], "links": gold["links"]},
        "recall": f"{len(gold_pairs & found_crash)}/{len(gold_pairs)}",
        "missed": sorted(gold_pairs - found_crash),
        "extra_on_gold_crashes": sorted(found_crash - gold_pairs),
        "whole_graph_vs_gold": {"only_graph": sorted(found_all - gold_pairs), "only_gold": sorted(gold_pairs - found_all)},
        "g_none": {"n": len(negatives), "false_positives": fp, "ids": negatives},
        "g_near": {"pairs": len(near), "crashes": len({n for _, n, _ in near}), "by_reason": near_kinds, "false_positives": near_fp},
        "traps": traps,
        "find_open_warnings_ms": {"median_wall": round(statistics.median(wall), 2), "p95_wall": round(sorted(wall)[int(0.95 * len(wall)) - 1], 2),
                                  "median_server": round(statistics.median(server), 2)},
    }
    report["pass"] = (not report["missed"] and not report["extra_on_gold_crashes"] and not fp and not near_fp
                      and not any(report["whole_graph_vs_gold"].values()) and all(traps.values())
                      and report["find_open_warnings_ms"]["median_wall"] < 20 and len(negatives) == 50)
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "g_none"} | {"g_none_fp": len(fp), "g_none_n": len(negatives)}, indent=1))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
