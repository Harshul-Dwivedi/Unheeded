"""Eval 2: the learning curve, hidden-half protocol (docs/evals.md). Proves Track 2 without a human.

1. Split the 16 gold crashes into halves A and B (8 each), stratified so both hold crashes with recorder links and without.
2. Copy `ntsb` to `ntsb_eval2` and delete every REITERATED_IN / RECLASSIFIED_IN edge into the gold crashes' reports,
   so the answer key is hidden from the graph.
3. Cold: find_candidates for every crash with default weights and no seeds.
4. Feedback on A: like a reviewer who reads the top 10, gold links in A's top 10 are confirmed and the rest rejected,
   through the same memory.record_feedback the Review queue uses (workspace ws_eval2_<split>).
5. Warm: re-run on B with that workspace's learned weights and A's confirmed links as seeds.
Swap halves, average. Pass if, on the held-out half, warm Hit@10 >= cold + 10 points, warm MRR > cold, and candidates
checked per crash warm <= cold. Writes eval/report_eval2.json; exits 1 if the bar is missed (the report says so honestly).

Also reported, not part of the bar (they say where the gain comes from):
  - unseen vs recurring: gold links whose recommendation was never confirmed in training vs ones that recur across
    crashes (A-13-013 is gold on 6 of 16). Seeds can only help recurring ones.
  - recurring_first: a baseline with no learning at all, cold ranking with training-confirmed recs moved to the top.
  - leave-one-out over all 16 crashes (train on 15) with a bootstrap 95% interval on the warm - cold Hit@10 gain.
  - per-signal AUC on the hidden gold (0.5 = coin flip), including Adamic-Adar weighting of shared findings.
"""
import random
import json
import re
import sys
from pathlib import Path

from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import DATA_DIR

OUT = Path(__file__).parent / "report_eval2.json"
EVAL_GRAPH = "ntsb_eval2"
DEEP = 200  # ranks are measured this deep; Hit@10 / MRR / checked use them
REVIEWED = 10  # what a reviewer reads per crash
# shared findings weighted Adamic-Adar style: a finding few crashes have counts more than one on 1,900 of them
SHARED_AA = """
MATCH (a:Accident {ntsb_num: $id}) WITH a
MATCH (a)-[:HAS_FINDING]->(x)<-[:HAS_FINDING]-(o:Accident) WHERE o <> a
WITH a, x, collect(o) AS os
WITH a, os, 1.0 / log(size(os) + 1) AS w
UNWIND os AS o
MATCH (o)<-[:ISSUED_FROM]-(r:Recommendation)
WHERE r.issued_date < a.event_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN r.srid, sum(w) AS n ORDER BY n DESC LIMIT 200
"""
RECORDER = re.compile(r"recorder|\bCVR\b|\bFDR\b|flight data|cockpit voice|image record|data monitoring", re.I)


def split(gold, texts):
    """Deterministic, stratified: recorder-link crashes first (by date), then the rest; alternate A, B."""
    rec = lambda i: any(RECORDER.search(texts.get(s, "")) for s in i["warnings"])
    order = sorted(gold["items"], key=lambda i: (not rec(i), i["event_date"]))
    a = [i["ntsb_num"] for i in order[0::2]]
    b = [i["ntsb_num"] for i in order[1::2]]
    return a, b, {i["ntsb_num"]: rec(i) for i in gold["items"]}


def ranks(g, crash, gold_srids, weights=None, seeds=()):
    res = T.find_candidates(g, ntsb_num=crash, weights=weights, seeds=seeds, limit=DEEP)
    ids = [c["id"] for c in res["candidates"]]
    return res, sorted(ids.index(s) + 1 for s in gold_srids if s in ids)


def auc(pos, neg):
    """P(a random gold candidate scores above a random non-gold one); ties count half."""
    if not pos or not neg:
        return None
    return round(sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg) / (len(pos) * len(neg)), 3)


def link_hits(results, gold_by_crash, seen):
    """Share of gold links in the top 10, split by whether their recommendation was confirmed in training."""
    out = {"unseen": [0, 0], "recurring": [0, 0]}
    for c, ids in results.items():
        for s in gold_by_crash[c]:
            k = "recurring" if s in seen else "unseen"
            out[k][1] += 1
            out[k][0] += s in ids[:10]
    return {k: {"in_top10": h, "links": n, "share": round(h / n, 3) if n else None} for k, (h, n) in out.items()}


def metrics(per_crash):
    """per_crash: {crash: sorted gold ranks}. Hit@10 by crash, MRR of first hit, candidates checked to first hit (cap 10)."""
    n = len(per_crash)
    first = [r[0] if r else None for r in per_crash.values()]
    return {"hit@10": round(100 * sum(1 for f in first if f and f <= 10) / n, 1),
            "mrr": round(sum(1 / f for f in first if f) / n, 4),
            "checked_per_crash": round(sum(min(f, 10) if f else 10 for f in first) / n, 2),
            "gold_links_in_top10": sum(1 for r in per_crash.values() for x in r if x <= 10),
            "first_hit_rank": dict(zip(per_crash, first))}


def run_split(admin, g, gold_by_crash, train, test, name):
    ws_name = f"eval2_{name}"
    try:
        M.drop_workspace(ws_name)
    except Exception:  # fresh
        pass
    ws = M.workspace(ws_name)
    inv = M.start_investigation(ws, f"Eval 2 split {name}: train on {len(train)} crashes")
    cold_res = {c: ranks(g, c, gold_by_crash[c])[0] for c in test}
    cold = {c: sorted(ids.index(s) + 1 for s in gold_by_crash[c] if s in ids)
            for c, ids in ((c, [x["id"] for x in r["candidates"]]) for c, r in cold_res.items())}
    confirmed = rejected = 0
    for c in train:  # feedback through the Review queue's own code path, on what a reviewer reads
        res, _ = ranks(g, c, gold_by_crash[c])
        gold = set(gold_by_crash[c])
        keep = res["candidates"][:REVIEWED]
        M.save_candidates(ws, inv, {**res, "candidates": keep})
        for x in keep:
            ok = x["id"] in gold
            M.record_feedback(ws, x["id"], c, "confirmed" if ok else "rejected", "eval2")
            confirmed, rejected = confirmed + ok, rejected + (not ok)
    weights, seeds = M.weights(ws), M.seeds(ws)
    seen = {s for s, _ in seeds}
    warm_res = {c: ranks(g, c, gold_by_crash[c], weights, seeds)[0] for c in test}
    warm_ids = {c: [x["id"] for x in r["candidates"]] for c, r in warm_res.items()}
    warm = {c: sorted(ids.index(s) + 1 for s in gold_by_crash[c] if s in ids) for c, ids in warm_ids.items()}
    cold_ids = {c: [x["id"] for x in r["candidates"]] for c, r in cold_res.items()}
    recur = {c: sorted(ids, key=lambda s: s not in seen) for c, ids in cold_ids.items()}  # stable: no learning, just recurrence
    recurring_first = {c: sorted(ids.index(s) + 1 for s in gold_by_crash[c] if s in ids) for c, ids in recur.items()}
    fitted_auc = auc(*_split_scores(cold_res, gold_by_crash, lambda x: T.score(x["signals"], weights)))
    default_auc = auc(*_split_scores(cold_res, gold_by_crash, lambda x: x["score"]))
    # ablation: which half of memory does the work
    weights_only = {c: ranks(g, c, gold_by_crash[c], weights)[1] for c in test}
    seeds_only = {c: ranks(g, c, gold_by_crash[c], None, seeds)[1] for c in test}
    M.drop_workspace(ws_name)
    return {"train": train, "test": test, "feedback": {"confirmed": confirmed, "rejected": rejected},
            "learned_weights": weights, "seeds": len(seeds), "cold": metrics(cold), "warm": metrics(warm),
            "weights_only": metrics(weights_only), "seeds_only": metrics(seeds_only),
            "recurring_first": metrics(recurring_first),
            "links_by_recurrence": {"cold": link_hits(cold_ids, gold_by_crash, seen), "warm": link_hits(warm_ids, gold_by_crash, seen)},
            "auc_on_test": {"default_weights": default_auc, "fitted_weights": fitted_auc}}


def _split_scores(results, gold_by_crash, value):
    pos, neg = [], []
    for c, r in results.items():
        for x in r["candidates"]:
            (pos if x["id"] in gold_by_crash[c] else neg).append(value(x))
    return pos, neg


def signal_auc(g, gold_by_crash):
    """Per-signal AUC over every crash's cold candidates on the hidden graph."""
    rows = []
    for c in gold_by_crash:
        res = T.find_candidates(g, ntsb_num=c, limit=DEEP)
        aa = dict(g.ro_query(SHARED_AA, {"id": c}).result_set)
        rows += [(x["id"] in gold_by_crash[c], {**x["signals"], "findings_aa": aa.get(x["id"], 0), "score": x["score"]})
                 for x in res["candidates"]]
    out = {}
    for k in ("text", "findings", "findings_aa", "events", "family", "score"):
        out[k] = auc([v.get(k, 0) for ok, v in rows if ok], [v.get(k, 0) for ok, v in rows if not ok])
    out["candidates"], out["gold"] = len(rows), sum(ok for ok, _ in rows)
    return out


def leave_one_out(g, gold_by_crash, boot=2000):
    """Train on 15 crashes, test on the 16th, for each crash; bootstrap the warm - cold Hit@10 gain over crashes."""
    cold, warm, unseen_hits, unseen_links = {}, {}, 0, 0
    feedback = {}
    for c in gold_by_crash:  # the reviewer's decisions on a crash do not depend on the fold
        res, _ = ranks(g, c, gold_by_crash[c])
        feedback[c] = res
        ids = [x["id"] for x in res["candidates"]]
        cold[c] = sorted(ids.index(s) + 1 for s in gold_by_crash[c] if s in ids)
    for c in gold_by_crash:
        ws_name = "eval2_loo"
        try:
            M.drop_workspace(ws_name)
        except Exception:  # fresh
            pass
        ws = M.workspace(ws_name)
        inv = M.start_investigation(ws, f"Eval 2 leave-one-out: hold out {c}")
        for t in gold_by_crash:
            if t == c:
                continue
            keep = feedback[t]["candidates"][:REVIEWED]
            M.save_candidates(ws, inv, {**feedback[t], "candidates": keep})
            for x in keep:
                M.record_feedback(ws, x["id"], t, "confirmed" if x["id"] in gold_by_crash[t] else "rejected", "eval2")
        seeds = M.seeds(ws)
        res, warm[c] = ranks(g, c, gold_by_crash[c], M.weights(ws), seeds)
        seen = {s for s, _ in seeds}
        ids = [x["id"] for x in res["candidates"]][:10]
        unseen_links += sum(s not in seen for s in gold_by_crash[c])
        unseen_hits += sum(s not in seen and s in ids for s in gold_by_crash[c])
        M.drop_workspace(ws_name)
    hit = lambda r: 1 if r and r[0] <= 10 else 0
    gains = [hit(warm[c]) - hit(cold[c]) for c in gold_by_crash]
    rnd, n = random.Random(0), len(gains)
    boots = sorted(sum(rnd.choice(gains) for _ in range(n)) / n * 100 for _ in range(boot))
    return {"cold": metrics(cold), "warm": metrics(warm), "gain_hit@10_points": round(100 * sum(gains) / n, 1),
            "gain_95ci": [round(boots[int(0.025 * boot)], 1), round(boots[int(0.975 * boot)], 1)],
            "unseen_gold_links_in_warm_top10": f"{unseen_hits}/{unseen_links}"}


def main():
    gold = json.load(open(DATA_DIR.parent / "derived" / "gold_links_strict_definition.json"))
    gold_by_crash = {i["ntsb_num"]: sorted(i["warnings"]) for i in gold["items"]}
    admin = connect()
    try:
        admin.select_graph(EVAL_GRAPH).delete()
    except Exception:  # no previous copy
        pass
    admin.connection.execute_command("GRAPH.COPY", "ntsb", EVAL_GRAPH)
    hidden = admin.select_graph(EVAL_GRAPH).query(
        "MATCH (:Recommendation)-[e:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident) "
        "WHERE a.ntsb_num IN $c DELETE e", {"c": list(gold_by_crash)}).relationships_deleted
    g = connect("agent_ro").select_graph(EVAL_GRAPH)
    still = sum(len(T.find_open_warnings(g, c)["warnings"]) for c in gold_by_crash)
    texts = dict(g.ro_query("MATCH (r:Recommendation) WHERE r.srid IN $s RETURN r.srid, r.text",
                            {"s": sorted({s for v in gold_by_crash.values() for s in v})}).result_set)
    a, b, recorder = split(gold, texts)
    try:
        s1 = run_split(admin, g, gold_by_crash, a, b, "a_to_b")
        s2 = run_split(admin, g, gold_by_crash, b, a, "b_to_a")
        loo = leave_one_out(g, gold_by_crash)
        sig = signal_auc(g, gold_by_crash)
    finally:
        admin.select_graph(EVAL_GRAPH).delete()
    avg = {k: {m: round((s1[k][m] + s2[k][m]) / 2, 4) for m in ("hit@10", "mrr", "checked_per_crash")}
           for k in ("cold", "warm", "weights_only", "seeds_only", "recurring_first")}
    bar = {"hit@10": avg["warm"]["hit@10"] >= avg["cold"]["hit@10"] + 10, "mrr": avg["warm"]["mrr"] > avg["cold"]["mrr"],
           "checked": avg["warm"]["checked_per_crash"] <= avg["cold"]["checked_per_crash"]}
    report = {"setup": {"hidden_edges": hidden, "linked_warnings_left_on_gold_crashes": still,
                        "halves": {"A": a, "B": b}, "recorder_crashes": {"A": sum(recorder[c] for c in a), "B": sum(recorder[c] for c in b)}},
              "splits": {"A->B": s1, "B->A": s2}, "average": avg, "bar": bar, "pass": all(bar.values()) and still == 0,
              "leave_one_out": loo, "signal_auc": sig,
              "auc_on_test": {k: round((s1["auc_on_test"][k] + s2["auc_on_test"][k]) / 2, 3) for k in s1["auc_on_test"]},
              "links_by_recurrence": {k: {sub: {"in_top10": s1["links_by_recurrence"][k][sub]["in_top10"] + s2["links_by_recurrence"][k][sub]["in_top10"],
                                              "links": s1["links_by_recurrence"][k][sub]["links"] + s2["links_by_recurrence"][k][sub]["links"]}
                                         for sub in ("unseen", "recurring")} for k in ("cold", "warm")}}
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps({"setup": report["setup"], "average": avg, "bar": bar, "pass": report["pass"],
                      "leave_one_out": loo, "signal_auc": sig, "auc_on_test": report["auc_on_test"],
                      "links_by_recurrence": report["links_by_recurrence"],
                      "learned_weights": {"A->B": s1["learned_weights"], "B->A": s2["learned_weights"]},
                      "feedback": {"A->B": s1["feedback"], "B->A": s2["feedback"]}}, indent=1))
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
