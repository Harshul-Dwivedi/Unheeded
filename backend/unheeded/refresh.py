"""Watch mode: load a newer CAROL export, diff it against the live graph, reload, and alert every workspace that follows
something the change reaches (docs/architecture.md "Modes", Track 3 "keeping knowledge current").

Per recommendation the diff reports: new recommendation, status change (code), closure, new letters, new repeat or
reclassification in a report. Alerts are raised after the reload, so BFS reach (tools.alert_reach) runs on the new record.
Run: python -m unheeded.refresh <dir with the three export files>   (writes `ntsb`; then run `python -m unheeded.enrich`)
"""
import sys
import time
from collections import Counter

from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import GRAPH, build, read, write

REPEAT_EDGES = ("REITERATED_IN", "RECLASSIFIED_IN")


def snapshot_graph(g):
    """What the live graph says, per recommendation: status, closed date, letter count, repeat (type, report) pairs."""
    recs = {s: {"code": c, "closed": d} for s, c, d in g.ro_query("MATCH (r:Recommendation) RETURN r.srid, r.status_code, r.closed_date").result_set}
    for s, n in g.ro_query("MATCH (:Letter)-[:ABOUT]->(r:Recommendation) RETURN r.srid, count(*)").result_set:
        recs[s]["letters"] = n
    rep = {}
    for s, t, p in g.ro_query("MATCH (r:Recommendation)-[e:REITERATED_IN|RECLASSIFIED_IN]->(p:Report) RETURN r.srid, type(e), p.display").result_set:
        rep.setdefault(s, set()).add((t, p))
    return recs, rep


def snapshot_model(model):
    displays = {r["report_num"]: r["display"] for r in model["nodes"]["Report"]}
    recs = {r["srid"]: {"code": r["status_code"], "closed": r.get("closed_date")} for r in model["nodes"]["Recommendation"]}
    letters = Counter(e["d"] for k, rows in model["edges"].items() if k[0] == "ABOUT" and k[1] == "Letter" for e in rows)
    for s, n in letters.items():
        recs[s]["letters"] = n
    rep = {}
    for k, rows in model["edges"].items():
        if k[0] in REPEAT_EDGES:
            for e in rows:
                rep.setdefault(e["s"], set()).add((k[0], displays.get(e["d"], e["d"])))
    return recs, rep


KINDS = ("status", "repeat", "letters", "new")  # what a workspace can choose to be told about (memory.alert_settings)


def diff(old, new):
    """[(srid, [(kind, sentence)])] for every recommendation whose record changed. Closures count as kind "status"."""
    (orecs, orep), (nrecs, nrep) = old, new
    out = []
    for s, n in sorted(nrecs.items()):
        o, changes = orecs.get(s), []
        if o is None:
            changes.append(("new", f"new recommendation, status {n['code']}"))
        else:
            if o["code"] != n["code"]:
                changes.append(("status", f"status {o['code']} → {n['code']}"))
            if not o.get("closed") and n.get("closed"):
                changes.append(("status", f"closed on {n['closed']}"))
            added = n.get("letters", 0) - o.get("letters", 0)
            if added > 0:
                changes.append(("letters", f"{added} new letter{'s' * (added > 1)}"))
            for t, p in sorted(nrep.get(s, set()) - orep.get(s, set())):
                changes.append(("repeat", f"{'repeated' if t == 'REITERATED_IN' else 'reclassified'} in {p}"))
        if changes:
            out.append((s, changes))
    return out


def refresh(data_dir, graph=GRAPH, workspaces=None, now=None):
    """Diff, reload, alert. Returns {"changes": [...], "alerts": {workspace: [...]}, "seconds": n}."""
    t0 = time.time()
    db = connect()  # admin: rewrites the official graph (agents never can)
    g = db.select_graph(graph)
    old = snapshot_graph(g)
    model = build(*read(data_dir))
    changes = diff(old, snapshot_model(model))
    write(g, model)
    reader = connect("agent_ro").select_graph(graph)
    names = workspaces if workspaces is not None else M.workspaces()
    alerts = {}
    wanted = {name: M.alert_settings(M.workspace(name)) for name in names}
    for srid, what in changes:
        reached = None
        for name in names:
            text = [t for kind, t in what if wanted[name].get(kind)]  # only what this workspace asked to be told about
            if not text:
                continue
            reached = reached if reached is not None else T.alert_reach(reader, srid)["reached"]
            hits = M.raise_alerts(M.workspace(name), srid, "; ".join(text), reached, now)
            if hits:
                alerts.setdefault(name, []).extend({"srid": srid, "change": text, **h} for h in hits)
    return {"changes": [{"srid": s, "change": [t for _, t in c]} for s, c in changes], "alerts": alerts, "seconds": round(time.time() - t0, 1)}


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: python -m unheeded.refresh <dir with the three CAROL export files>")
    r = refresh(sys.argv[1])
    print(f"{len(r['changes'])} recommendations changed; alerts in {len(r['alerts'])} workspace(s); {r['seconds']} s")
    for c in r["changes"][:30]:
        print(f"  {c['srid']}: {'; '.join(c['change'])}")
    for ws, hits in r["alerts"].items():
        print(f"  ws_{ws}: {len(hits)} alert(s)")
    print("now run: python -m unheeded.enrich  (embeddings, families, impact scores)")


if __name__ == "__main__":
    main()
