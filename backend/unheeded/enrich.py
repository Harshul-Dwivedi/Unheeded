"""Derived properties on the `ntsb` graph (docs/algorithms.md), run after the loader.

  text_embedding / cause_embedding  local model, vector indexes for KNN
  family_id                         algo.labelPropagation over probable-cause findings (hubs pruned) + defining events
  impact_score                      algo.pageRank over crash <-> linked-warning edges (temporary graph)

Run: python -m unheeded.enrich
"""
import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np

from unheeded import queries as Q
from unheeded.loader import GRAPH, batches, connect

MODEL, DIM = "BAAI/bge-small-en-v1.5", 384
CACHE = Path(__file__).resolve().parents[1] / ".cache"  # embeddings.npy + keys.json, keyed by sha1(model|text)


def embed(g):
    from fastembed import TextEmbedding
    keys = CACHE / "keys.json"
    cache = dict(zip(json.loads(keys.read_text()), np.load(CACHE / "embeddings.npy"))) if keys.exists() else {}
    model = None
    for label, key, src, dst in (("Recommendation", "srid", "text", "text_embedding"),
                                 ("Accident", "ntsb_num", "probable_cause", "cause_embedding")):
        rows = g.query(f"MATCH (n:{label}) WHERE n.{src} IS NOT NULL RETURN n.{key}, n.{src}").result_set
        texts = [re.sub(r"^TO THE [^:]{1,200}:\s*", "", t) for _, t in rows]  # drop the addressee header
        hashes = [hashlib.sha1(f"{MODEL}|{t}".encode()).hexdigest() for t in texts]
        todo = [t for t, h in zip(texts, hashes) if h not in cache]
        if todo:
            model = model or TextEmbedding(MODEL)
            for t, v in zip(todo, model.embed(todo)):
                cache[hashlib.sha1(f"{MODEL}|{t}".encode()).hexdigest()] = v
        for b in batches([{"k": k, "v": cache[h].tolist()} for (k, _), h in zip(rows, hashes)], 500):
            g.query(f"UNWIND $rows AS row MATCH (n:{label} {{{key}: row.k}}) SET n.{dst} = vecf32(row.v)", {"rows": b})
        g.query(f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.{dst}) OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}")
        print(f"{dst:16} {len(rows):6} ({len(todo)} new)")
    CACHE.mkdir(exist_ok=True)
    keys.write_text(json.dumps(list(cache)))
    np.save(CACHE / "embeddings.npy", np.array(list(cache.values()), dtype=np.float32))


def families(g):
    coded = {r[0] for r in g.query("MATCH (a:Accident) WHERE (a)-[:HAS_FINDING]->() OR (a)-[:HAD_EVENT]->() RETURN a.ntsb_num").result_set}
    rows = g.query("CALL algo.labelPropagation({nodeLabels: ['Accident', 'FindingCode', 'EventType'], "
                   "relationshipTypes: ['HAS_PC_FINDING', 'HAD_DEFINING_EVENT'], maxIterations: 20}) YIELD node, communityId "  # loader.HUB_SHARE
                   "WHERE node:Accident RETURN node.ntsb_num, communityId").result_set
    fam = [{"k": n, "v": c} for n, c in rows if n in coded]
    for b in batches(fam):
        g.query("UNWIND $rows AS row MATCH (a:Accident {ntsb_num: row.k}) SET a.family_id = row.v", {"rows": b})
    print(f"family_id        {len(fam):6} accidents, {len({r['v'] for r in fam})} families")


def impact(db, g):
    links = [{"a": r[0], "r": r[4]} for r in g.query(Q.ALL_LINKED_CRASHES).result_set]
    t = db.select_graph("tmp_links")
    try:
        t.delete()
    except Exception:  # not there
        pass
    # PageRank takes (label, relType) and no weights in v4.20.7, so one label. Edges both ways: one-way
    # crash -> warning splits a crash's rank across its warnings and buries A-16-035 (Nome has 8).
    # ponytail: on ~54 links this tracks linked-crash count; it earns more once candidate links join the projection
    t.query("UNWIND $rows AS row MERGE (a:N {id: row.a}) WITH a, row UNWIND row.r AS s MERGE (r:N {id: s, rec: true}) "
            "CREATE (a)-[:CITES]->(r) CREATE (r)-[:CITES]->(a)", {"rows": links})
    scores = [{"k": k, "v": s} for k, s in t.query("CALL algo.pageRank('N', 'CITES') YIELD node, score "
                                                   "WHERE node.rec RETURN node.id, score").result_set]
    t.delete()
    g.query("UNWIND $rows AS row MATCH (r:Recommendation {srid: row.k}) SET r.impact_score = row.v", {"rows": scores})
    print(f"impact_score     {len(scores):6} recommendations")


def main():
    t0 = time.time()
    db = connect()
    g = db.select_graph(GRAPH)
    embed(g)
    families(g)
    impact(db, g)
    print(f"enriched in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
