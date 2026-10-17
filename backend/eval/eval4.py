"""Eval 4: graph necessity and latency (docs/evals.md).

  chain        supersession chain (SUPERSEDED_BY*1..10, both ways) for every rec that has one: max < 10 ms
  path         shortest path crash -> its oldest linked warning (SPpaths) for all gold crashes: max < 10 ms
  families     labelPropagation families (size >= 5) cover >= 80% of coded accidents
  pagerank     top 5 by impact_score includes A-13-013 and A-16-035
  memory       two investigations at once in different workspaces, and two in the same one: no cross-talk
  latency      every tool call of the gold-crash runs, read back from Step nodes: p95 < 50 ms
Times are FalkorDB server times (`run_time_ms`). Writes eval/report_eval4.json; exits 1 on failure.
"""
import asyncio
import json
import statistics
import sys
from pathlib import Path

from unheeded import agents as A
from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import DATA_DIR
from unheeded.stub_llm import StubChat

OUT = Path(__file__).parent / "report_eval4.json"


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[max(0, int(p * len(xs)) - 1)], 2)


def isolation(name, inv_ids):
    g = M.reader(name)
    invs = {r[0] for r in g.ro_query("MATCH (i:Investigation) RETURN i.id").result_set}
    foreign_tasks = g.ro_query("MATCH (t:Task)-[:FOR]->(i:Investigation) WHERE NOT i.id IN $ids RETURN count(t)", {"ids": inv_ids}).result_set[0][0]
    per_inv = {i: [(t["kind"], t["status"]) for t in M.board(g, i)] for i in inv_ids}
    return {"investigations": sorted(invs), "expected": sorted(inv_ids), "foreign_tasks": foreign_tasks, "tasks": per_inv,
            "ok": invs == set(inv_ids) and foreign_tasks == 0
                  and all(v == [("resolve", "done"), ("analyse", "done"), ("draft_brief", "done")] for v in per_inv.values())}


async def runs():
    gold = json.load(open(DATA_DIR.parent / "derived" / "gold_links_strict_definition.json"))
    tag = "eval4"
    out = {"names": [f"{tag}_a", f"{tag}_b", f"{tag}_same"]}
    # two investigations at once, different workspaces
    a, b = await asyncio.gather(A.run("Was the Nome crash warned about?", f"{tag}_a", model=StubChat()),
                                A.run("Was the Kekaha Hawaii crash in 2019 warned about?", f"{tag}_b", model=StubChat()))
    out["different"] = [isolation(f"{tag}_a", [a["investigation"]]), isolation(f"{tag}_b", [b["investigation"]])]
    # two at once in the same workspace
    c, d = await asyncio.gather(A.run("Was the Nome crash warned about?", f"{tag}_same", model=StubChat()),
                                A.run("Was the Teterboro crash warned about?", f"{tag}_same", model=StubChat()))
    out["same"] = isolation(f"{tag}_same", [c["investigation"], d["investigation"]])
    out["same"]["subjects_ok"] = (c["facts"]["accident"]["ntsb_num"], d["facts"]["accident"]["ntsb_num"]) == ("ANC25MA018", "CEN17MA183")
    # latency: every gold crash, then read the Step nodes back
    for item in gold["items"]:
        await A.run(f"Was the crash {item['ntsb_num']} warned about?", f"{tag}_lat", model=StubChat())
    out["names"].append(f"{tag}_lat")
    rows = M.reader(f"{tag}_lat").ro_query("MATCH (s:Step) WHERE s.agent IN ['scout', 'analyst', 'clerk'] AND s.ms IS NOT NULL "
                                           "RETURN s.tool, s.ms").result_set
    out["steps"] = rows
    return out


def main():
    g = connect("agent_ro").select_graph("ntsb")
    report = {}

    srids = [r[0] for r in g.ro_query("MATCH (r:Recommendation) WHERE (r)-[:SUPERSEDED_BY]->() OR ()-[:SUPERSEDED_BY]->(r) RETURN r.srid").result_set]
    chain = [T.trace_chain(g, s)["ms"] for s in srids]
    report["chain"] = {"recs": len(srids), "p50_ms": pct(chain, .5), "max_ms": round(max(chain), 2), "ok": max(chain) < 10}

    gold = json.load(open(DATA_DIR.parent / "derived" / "gold_links_strict_definition.json"))
    paths = []
    for item in gold["items"]:
        oldest = min(item["warnings"], key=lambda s: item["warnings"][s]["issued"])
        p = T.explain_path(g, oldest, item["ntsb_num"])
        paths.append({"crash": item["ntsb_num"], "srid": oldest, "hops": len(p["path"]["edges"]) if p["path"] else None, "ms": p["ms"]})
    report["path"] = {"crashes": len(paths), "max_ms": max(x["ms"] for x in paths), "all_found": all(x["hops"] for x in paths),
                      "ok": all(x["hops"] for x in paths) and max(x["ms"] for x in paths) < 10, "paths": paths}

    coded, sizes = g.ro_query("MATCH (a:Accident) WHERE (a)-[:HAS_FINDING]->() OR (a)-[:HAD_EVENT]->() RETURN count(a)").result_set[0][0], \
        g.ro_query("MATCH (a:Accident) WHERE a.family_id IS NOT NULL RETURN a.family_id, count(*)").result_set
    covered = sum(n for _, n in sizes if n >= 5)
    report["families"] = {"coded": coded, "families": len(sizes), "in_families_of_5_plus": covered,
                          "share": round(covered / coded, 3), "largest": max(n for _, n in sizes), "ok": covered / coded >= 0.8}

    top = [r[0] for r in g.ro_query("MATCH (r:Recommendation) WHERE r.impact_score IS NOT NULL RETURN r.srid ORDER BY r.impact_score DESC LIMIT 5").result_set]
    report["pagerank"] = {"top5": top, "ok": {"A-13-013", "A-16-035"} <= set(top)}

    r = None
    try:
        r = asyncio.run(runs())
        report["memory"] = {"different_workspaces": r["different"], "same_workspace": r["same"],
                            "ok": all(x["ok"] for x in r["different"]) and r["same"]["ok"] and r["same"]["subjects_ok"]}
        ms = [m for _, m in r["steps"]]
        by_tool = {}
        for tool, m in r["steps"]:
            by_tool.setdefault(tool, []).append(m)
        report["latency"] = {"tool_calls": len(ms), "p50_ms": pct(ms, .5), "p95_ms": pct(ms, .95), "max_ms": round(max(ms), 2),
                             "by_tool_p95": {t: pct(v, .95) for t, v in sorted(by_tool.items())}, "ok": pct(ms, .95) < 50}
    finally:
        for name in (r or {}).get("names", ["eval4_a", "eval4_b", "eval4_same", "eval4_lat"]):
            try:
                M.drop_workspace(name)
            except Exception:  # never created
                pass
    report["pass"] = all(v["ok"] for v in report.values() if isinstance(v, dict) and "ok" in v)
    OUT.write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({k: ({kk: vv for kk, vv in v.items() if kk not in ("paths", "different_workspaces", "same_workspace")}
                          if isinstance(v, dict) else v) for k, v in report.items()}, indent=1, default=str))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
