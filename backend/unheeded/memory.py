"""Workspace memory (docs/graph-schema.md layer 2): one FalkorDB graph per workspace, `ws_<name>`, written only as the
ws_writer user, whom FalkorDB refuses any key outside ws_* (so `ntsb` stays untouchable).

Plain code in the agent flow calls these; the LLM gets no raw write tool:
  episodic    Investigation + Step chain (FIRST/NEXT/LAST)    log_step, steps
  coordination Task with leases, Agent                         create_task, claim_task, complete_task, board
  semantic    Candidate (proposed -> confirmed/rejected)       save_candidates, record_feedback, undo_feedback, belief_on, seeds
  procedural  Rule hit/miss counts -> weights                  rules, weights
  action      Brief draft -> gate -> awaiting_approval|blocked -> approved|rejected   save_brief, update_brief, decide_brief
  persistent  history search, Watch + Alert                    history, add_watch, raise_alerts
Reads use RO queries, so `reader(name)` (agent_ro) can call steps, board, belief_on, seeds, rules, weights, history.
Times here are epoch milliseconds (sub-day order matters); `ntsb` dates stay yyyymmdd.
"""
import json
import re
import time
import uuid
from functools import lru_cache

import numpy as np

from unheeded.config import connect
from unheeded.tools import DEFAULT_WEIGHTS, features

NAME = re.compile(r"^[a-z0-9_]{1,40}$")
LEASE_MS = 60_000
WATCH_KINDS = ("Recommendation", "Accident", "Organization")
SCHEMA = [
    "CREATE INDEX FOR (n:Investigation) ON (n.id)", "CREATE INDEX FOR (n:Step) ON (n.id)",
    "CREATE INDEX FOR (n:Task) ON (n.id)", "CREATE INDEX FOR (n:Task) ON (n.status)",
    "CREATE INDEX FOR (n:Agent) ON (n.name)", "CREATE INDEX FOR (n:Candidate) ON (n.key)",
    "CREATE INDEX FOR (n:Candidate) ON (n.status)", "CREATE INDEX FOR (n:Rule) ON (n.signal)",
    "CREATE INDEX FOR (n:Watch) ON (n.key)", "CREATE INDEX FOR (n:Brief) ON (n.id)",
    "CALL db.idx.fulltext.createNodeIndex('Investigation', 'question', 'outcome')",
]
_ready = set()


def now_ms():
    return int(time.time() * 1000)


def new_id():
    return uuid.uuid4().hex[:12]


@lru_cache(maxsize=1)
def _db():
    return connect("ws_writer")


def workspace(name):
    """Graph handle for ws_<name>; creates its indexes on first use. Names are [a-z0-9_]{1,40}."""
    if not NAME.match(name or ""):
        raise ValueError(f"bad workspace name {name!r}")
    g = _db().select_graph(f"ws_{name}")
    if name not in _ready:
        for q in SCHEMA:
            try:
                g.query(q)
            except Exception as e:  # FalkorDB errors on re-creating an index (FALKORDB-ISSUES #17)
                if "already indexed" not in str(e):
                    raise
        _ready.add(name)
    return g


def workspaces():
    return sorted(n[3:] for n in _db().list_graphs() if n.startswith("ws_"))


def drop_workspace(name):
    workspace(name).delete()
    _ready.discard(name)


def _one(g, _query, **p):
    rows = g.query(_query, p).result_set
    return rows[0] if rows else None


def _ro(g, _query, **p):
    """Reads use GRAPH.RO_QUERY so agents can read memory as agent_ro (who may not run GRAPH.QUERY at all).
    A workspace nobody has written to yet has no graph key: that reads as empty, not as an error."""
    try:
        return g.ro_query(_query, p).result_set
    except Exception as e:
        if "empty key" in str(e):
            return []
        raise


def reader(name):
    """Read-only handle on ws_<name> (agent_ro): for agents and the MCP server, never for writes."""
    if not NAME.match(name or ""):
        raise ValueError(f"bad workspace name {name!r}")
    return connect("agent_ro").select_graph(f"ws_{name}")


# --- episodic: investigations and the step chain -------------------------------------------------------------------

def start_investigation(g, question, inv_id=None, now=None):
    inv_id = inv_id or new_id()
    g.query("MERGE (i:Investigation {id: $id}) ON CREATE SET i.question = $q, i.started = $now, i.status = 'running'",
            {"id": inv_id, "q": question, "now": now or now_ms()})
    return inv_id


def finish_investigation(g, inv_id, outcome, now=None):
    return bool(_one(g, "MATCH (i:Investigation {id: $id}) SET i.finished = $now, i.outcome = $o, i.status = 'done' RETURN i.id",
                     id=inv_id, o=outcome, now=now or now_ms()))


# Appends in one query: O(1) via the LAST pointer, idempotent on step id (a retried step is not added twice)
LOG_STEP = """
MATCH (i:Investigation {id: $inv})
OPTIONAL MATCH (dup:Step {id: $id})
WITH i, dup WHERE dup IS NULL
OPTIONAL MATCH (i)-[l:LAST]->(p:Step)
CREATE (s:Step {id: $id, n: coalesce(p.n, 0) + 1, agent: $agent, tool: $tool, cypher: $cypher,
                result_count: $rc, ms: $ms, args: $args, at: $now})
CREATE (s)-[:IN]->(i)
FOREACH (x IN CASE WHEN p IS NULL THEN [1] ELSE [] END | CREATE (i)-[:FIRST]->(s))
FOREACH (x IN CASE WHEN p IS NULL THEN [] ELSE [p] END | CREATE (x)-[:NEXT]->(s))
DELETE l
CREATE (i)-[:LAST]->(s)
RETURN s.n
"""


def log_step(g, inv_id, agent, tool, cypher=None, result_count=None, ms=None, args=None, step_id=None, now=None):
    """Returns the step's position, or None when step_id was already logged."""
    row = _one(g, LOG_STEP, inv=inv_id, id=step_id or new_id(), agent=agent, tool=tool, cypher=cypher, rc=result_count,
               ms=ms, args=json.dumps(args) if args is not None else None, now=now or now_ms())
    return row[0] if row else None


def steps(g, inv_id):
    """'How we got here': the whole chain as one path, FIRST -> NEXT* -> LAST."""
    rows = _ro(g, "MATCH (f:Step)<-[:FIRST]-(i:Investigation {id: $id})-[:LAST]->(l:Step) MATCH p = (f)-[:NEXT*0..1000]->(l) "
                  "RETURN [s IN nodes(p) | s {.n, .agent, .tool, .cypher, .result_count, .ms, .at, .args}]", id=inv_id)
    return rows[0][0] if rows else []


# --- coordination: tasks with leases ------------------------------------------------------------------------------

def create_task(g, inv_id, kind, payload=None, task_id=None, now=None):
    task_id = task_id or new_id()
    row = _one(g, "MATCH (i:Investigation {id: $inv}) MERGE (t:Task {id: $id}) "
                  "ON CREATE SET t.kind = $kind, t.status = 'queued', t.payload = $payload, t.created = $now, t.attempts = 0 "
                  "MERGE (t)-[:FOR]->(i) RETURN t.id",
               inv=inv_id, id=task_id, kind=kind, payload=json.dumps(payload or {}), now=now or now_ms())
    return row[0] if row else None


# Atomic: find-and-mark in one write query, so two agents can never hold the same task (race test in tests/test_memory.py)
CLAIM = """
MATCH (t:Task)-[:FOR]->(i:Investigation)
WHERE (t.status = 'queued' OR (t.status = 'claimed' AND t.lease_until < $now)) AND ($kind IS NULL OR t.kind = $kind)
  AND ($inv IS NULL OR i.id = $inv)
WITH t ORDER BY t.created, t.id LIMIT 1
MERGE (a:Agent {name: $agent})
SET t.status = 'claimed', t.claimed_by = $agent, t.lease_until = $now + $lease, t.attempts = t.attempts + 1
CREATE (t)-[:CLAIMED_BY {at: $now}]->(a)
RETURN t.id, t.kind, t.payload, t.attempts
"""


def claim_task(g, agent, kind=None, inv_id=None, lease_ms=LEASE_MS, now=None):
    """Next queued task (optionally of one kind / one investigation), or one whose lease ran out. None if none."""
    row = _one(g, CLAIM, agent=agent, kind=kind, inv=inv_id, lease=lease_ms, now=now or now_ms())
    return {"id": row[0], "kind": row[1], "payload": json.loads(row[2] or "{}"), "attempts": row[3]} if row else None


def complete_task(g, task_id, agent, result=None, now=None):
    """Only the lease holder, before expiry. False = lease lost (someone else may own it now): fail closed."""
    return bool(_one(g, "MATCH (t:Task {id: $id}) WHERE t.status = 'claimed' AND t.claimed_by = $agent AND t.lease_until >= $now "
                        "SET t.status = 'done', t.finished = $now, t.result = $result RETURN t.id",
                     id=task_id, agent=agent, result=json.dumps(result) if result is not None else None, now=now or now_ms()))


def board(g, inv_id=None):
    """Task nodes for the agent board UI."""
    rows = _ro(g, "MATCH (t:Task)-[:FOR]->(i:Investigation) WHERE $inv IS NULL OR i.id = $inv "
                  "RETURN t.id, t.kind, t.status, t.claimed_by, t.lease_until, t.attempts, i.id ORDER BY t.created, t.id",
               inv=inv_id)
    keys = ["id", "kind", "status", "claimed_by", "lease_until", "attempts", "investigation"]
    return [dict(zip(keys, r)) for r in rows]


# --- semantic: candidates -----------------------------------------------------------------------------------------

def _key(srid, ntsb_num):
    return f"{srid}|{ntsb_num}"


def save_candidates(g, inv_id, result, now=None):
    """Store a tools.find_candidates result (and only that): unverified proposals, MERGEd on (srid, crash)."""
    if result.get("verified") is not False or result.get("kind") not in ("recommendation", "accident"):
        raise ValueError("expects a tools.find_candidates result")
    t, rows = result["target"], []
    for c in result["candidates"]:
        srid, ntsb = (c["id"], t) if result["kind"] == "recommendation" else (t, c["id"])
        rows.append({"key": _key(srid, ntsb), "srid": srid, "ntsb_num": ntsb, "score": c["score"],
                     "evidence": json.dumps(c["signals"]), "signals": sorted(c["signals"])})
    g.query("MATCH (i:Investigation {id: $inv}) UNWIND $rows AS row MERGE (c:Candidate {key: row.key}) "
            "ON CREATE SET c.srid = row.srid, c.ntsb_num = row.ntsb_num, c.status = 'proposed', c.proposed_at = $now "
            "SET c.score = row.score, c.evidence = row.evidence, c.signals = row.signals, c.weights = $w, c.recorded_at = $now "
            "MERGE (i)-[:PROPOSED]->(c)",
            {"inv": inv_id, "rows": rows, "w": json.dumps(result.get("weights")), "now": now or now_ms()})
    return len(rows)


# Compare-and-set on status, so a candidate is counted once even if two reviewers click at the same time
FEEDBACK = """
MATCH (c:Candidate {key: $key}) WHERE c.status = 'proposed'
SET c.status = $verdict, c.decided_at = $now, c.decided_by = $reviewer
WITH c
UNWIND c.signals AS sig
MERGE (r:Rule {signal: sig})
ON CREATE SET r.hits = 0, r.misses = 0
SET r.hits = r.hits + $hit, r.misses = r.misses + 1 - $hit
MERGE (r)-[:LEARNED_FROM]->(c)
RETURN count(r)
"""


def record_feedback(g, srid, ntsb_num, verdict, reviewer, now=None):
    """Review queue: confirmed / rejected. False if unknown or already decided (no double counting)."""
    if verdict not in ("confirmed", "rejected"):
        raise ValueError("verdict must be confirmed or rejected")
    row = _one(g, FEEDBACK, key=_key(srid, ntsb_num), verdict=verdict, reviewer=reviewer,
               hit=1 if verdict == "confirmed" else 0, now=now or now_ms())
    return bool(row and row[0])


# Undo: reverse a decision atomically, keep an immutable Revision node, and take the decision back out of the rule counts
UNDO = """
MATCH (c:Candidate {key: $key}) WHERE c.status IN ['confirmed', 'rejected']
WITH c, c.status AS was, c.decided_by AS by_before, c.decided_at AS at_before
CREATE (v:Revision {from_status: was, decided_by: by_before, decided_at: at_before, undone_by: $by, at: $now})-[:OF]->(c)
SET c.status = 'proposed', c.decided_at = null, c.decided_by = null
WITH c, was
OPTIONAL MATCH (r:Rule)-[l:LEARNED_FROM]->(c)
SET r.hits = r.hits - CASE WHEN was = 'confirmed' THEN 1 ELSE 0 END, r.misses = r.misses - CASE WHEN was = 'rejected' THEN 1 ELSE 0 END
DELETE l
RETURN was, count(r)
"""


def undo_feedback(g, srid, ntsb_num, reviewer, now=None):
    """Review queue undo. False if the candidate is unknown or not decided."""
    return bool(_one(g, UNDO, key=_key(srid, ntsb_num), by=reviewer, now=now or now_ms()))


def belief_on(g, srid, ntsb_num, at_ms):
    """Bi-temporal read: what this workspace believed about the pair at a moment (None = not yet proposed)."""
    rows = _ro(g, "MATCH (c:Candidate {key: $key}) RETURN CASE WHEN c.proposed_at > $t THEN null "
                  "WHEN c.decided_at IS NOT NULL AND c.decided_at <= $t THEN c.status ELSE 'proposed' END",
               key=_key(srid, ntsb_num), t=at_ms)
    return rows[0][0] if rows else None


def seeds(g):
    """Confirmed (srid, ntsb_num) pairs: retrieval seeds for tools.find_candidates."""
    return [tuple(r) for r in _ro(g, "MATCH (c:Candidate {status: 'confirmed'}) RETURN c.srid, c.ntsb_num ORDER BY c.key")]


# --- procedural: rules -> weights ---------------------------------------------------------------------------------

def rules(g):
    rows = _ro(g, "MATCH (r:Rule) RETURN r.signal, r.hits, r.misses ORDER BY r.signal")
    decided = (_ro(g, "MATCH (c:Candidate) WHERE c.status IN ['confirmed', 'rejected'] "
                      "RETURN count(c), sum(CASE WHEN c.status = 'confirmed' THEN 1 ELSE 0 END)") or [[0, 0]])[0]
    return {"signals": {s: {"hits": h, "misses": m} for s, h, m in rows}, "decided": decided[0], "confirmed": int(decided[1] or 0)}


MIN_DECISIONS = 10  # below this (or with only one outcome seen) the defaults stand


def fit_weights(rows, prior=None, lam=0.05, steps=2000, lr=0.5):
    """Logistic regression of confirmed (1) / rejected (0) on the signal values, pulled toward the default weights
    (L2 to the prior). Deterministic plain code (numpy gradient descent), no model involved.
    rows: [(signals dict, confirmed bool)]. Signals that never vary in the data keep their prior weight; the learned
    ones are rescaled to the prior's total so they don't swamp those (e.g. seed, which only exists after confirmations).
    Negative weights are clipped to 0: a signal can be ignored but never counts against a candidate."""
    prior = dict(prior or DEFAULT_WEIGHTS)
    keys = list(prior)
    X = np.array([[features(sig)[k] for k in keys] for sig, _ in rows], dtype=float)
    y = np.array([1.0 if ok else 0.0 for _, ok in rows])
    p0 = np.array([prior[k] for k in keys])
    varies = X.std(axis=0) > 1e-9
    w, b = p0.copy(), 0.0
    for _ in range(steps):
        z = 1 / (1 + np.exp(-(X @ w + b)))
        gw = X.T @ (z - y) / len(y) + 2 * lam * (w - p0)
        w -= lr * np.where(varies, gw, 0)  # unvarying signals: no evidence, keep the prior
        b -= lr * float(np.mean(z - y))
    w = np.clip(w, 0, None)
    if w[varies].sum() > 0:
        w[varies] *= p0[varies].sum() / w[varies].sum()
    return {k: round(float(v), 4) for k, v in zip(keys, w)}


def decided_rows(g):
    return [(json.loads(e or "{}"), st == "confirmed") for e, st in
            _ro(g, "MATCH (c:Candidate) WHERE c.status IN ['confirmed', 'rejected'] RETURN c.evidence, c.status ORDER BY c.key")]


def weights(g):
    """This workspace's ranking weights: fitted to its review decisions once there are enough of them."""
    rows = decided_rows(g)
    if len(rows) < MIN_DECISIONS or len({ok for _, ok in rows}) < 2:
        return dict(DEFAULT_WEIGHTS)
    return fit_weights(rows)


# --- action: briefs (the approval gate, task 6, moves draft -> blocked / approved) -------------------------------

def save_brief(g, inv_id, text, citations, now=None):
    brief_id = new_id()
    g.query("MATCH (i:Investigation {id: $inv}) CREATE (b:Brief {id: $id, text: $text, citations: $cites, status: 'draft', "
            "created: $now}) CREATE (i)-[:PRODUCED]->(b)",
            {"inv": inv_id, "id": brief_id, "text": text, "cites": list(citations), "now": now or now_ms()})
    return brief_id


def update_brief(g, brief_id, status, text=None, citations=None, verdict=None, thread=None, now=None):
    """thread: the paused LangGraph run that can still decide this brief."""
    g.query("MATCH (b:Brief {id: $id}) SET b.status = $status, b.updated = $now, b.text = coalesce($text, b.text), "
            "b.citations = coalesce($cites, b.citations), b.verdict = coalesce($verdict, b.verdict), b.thread = coalesce($thread, b.thread)",
            {"id": brief_id, "status": status, "text": text, "cites": list(citations) if citations is not None else None,
             "verdict": json.dumps(verdict) if verdict is not None else None, "thread": thread, "now": now or now_ms()})


def decide_brief(g, brief_id, decision, reviewer, note=None, now=None):
    """Human decision. 'approved' only from 'awaiting_approval' (i.e. the gate passed): compare-and-set, fail closed."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    allowed = ["awaiting_approval"] if decision == "approved" else ["awaiting_approval", "blocked"]
    edge = "APPROVED_BY" if decision == "approved" else "REJECTED_BY"
    row = _one(g, f"MATCH (b:Brief {{id: $id}}) WHERE b.status IN $allowed SET b.status = $decision, b.updated = $now "
                  f"CREATE (a:Approval {{by: $by, decision: $decision, note: $note, at: $now}}) CREATE (b)-[:{edge}]->(a) RETURN b.id",
               id=brief_id, allowed=allowed, decision=decision, by=reviewer, note=note, now=now or now_ms())
    return bool(row)


# --- persistent context: history, watches, alerts -----------------------------------------------------------------

def history(g, text=None, limit=10):
    """Past investigations: full-text over question/outcome when `text` is given, else most recent."""
    words = re.findall(r"[A-Za-z0-9]{2,}", text or "")
    head = ("CALL db.idx.fulltext.queryNodes('Investigation', $q) YIELD node AS i, score " if words
            else "MATCH (i:Investigation) WITH i, 0 AS score ")
    rows = _ro(g, head + "OPTIONAL MATCH (i)-[:PROPOSED]->(c:Candidate) "
                         "RETURN i.id, i.question, i.outcome, i.started, count(c) AS candidates, score "
                         "ORDER BY score DESC, i.started DESC LIMIT $limit",
               q="|".join(words), limit=max(1, min(int(limit), 50)))
    keys = ["id", "question", "outcome", "started", "candidates"]
    return [dict(zip(keys, r)) for r in rows]


def add_watch(g, kind, target_id, note=None, now=None):
    if kind not in WATCH_KINDS:
        raise ValueError(f"kind must be one of {WATCH_KINDS}")
    g.query("MERGE (w:Watch {key: $key}) ON CREATE SET w.kind = $kind, w.target_id = $id, w.note = $note, w.created = $now",
            {"key": f"{kind}|{target_id}", "kind": kind, "id": target_id, "note": note, "now": now or now_ms()})


ALERT_DEFAULTS = {"status": True, "repeat": True, "letters": True, "new": False}  # Watch page "Tell me about"


def alert_settings(g):
    rows = _ro(g, "MATCH (s:Settings {id: 'alerts'}) RETURN s {.status, .repeat, .letters, .new}")
    return {**ALERT_DEFAULTS, **{k: v for k, v in (rows[0][0] if rows else {}).items() if v is not None}}


def set_alert_settings(g, settings):
    clean = {k: bool(settings[k]) for k in ALERT_DEFAULTS if k in settings}
    g.query("MERGE (s:Settings {id: 'alerts'}) SET s += $s", {"s": clean})
    return alert_settings(g)


def raise_alerts(g, srid, change, reached, now=None):
    """Watch mode: given tools.alert_reach(srid)['reached'], record an Alert on every watch it hits."""
    keys = sorted({f"{r['kind']}|{v}" for r in reached for v in (r["id"], r.get("acronym")) if v})
    res = g.query("UNWIND $keys AS k MATCH (w:Watch {key: k}) "
                  "CREATE (a:Alert {srid: $srid, change: $change, at: $now})-[:FOR]->(w) RETURN w.kind, w.target_id",
                  {"keys": keys, "srid": srid, "change": change, "now": now or now_ms()})
    return [{"kind": k, "target_id": t} for k, t in res.result_set]


def fan_out_alerts(srid, change, reached, now=None):
    """Every workspace at once (one alert per hit watch per workspace). Returns {workspace: hits}."""
    return {name: hits for name in workspaces() if (hits := raise_alerts(workspace(name), srid, change, reached, now))}


def investigations(g, limit=50):
    """Investigation list for the UI: newest first, with the latest brief's status."""
    rows = _ro(g, "MATCH (i:Investigation) OPTIONAL MATCH (i)-[:PRODUCED]->(b:Brief) "
                  "RETURN i {.id, .question, .status, .outcome, .started, .finished}, b {.id, .status, .thread, .created} "
                  "ORDER BY i.started DESC LIMIT $limit", limit=max(1, min(int(limit), 200)))
    return [{**i, "brief": b} for i, b in rows]


def investigation(g, inv_id):
    """One investigation with its steps, tasks, briefs (with gate verdicts) and candidates."""
    head = _ro(g, "MATCH (i:Investigation {id: $id}) RETURN i {.id, .question, .status, .outcome, .started, .finished}", id=inv_id)
    if not head:
        return None
    briefs = [{**b, "verdict": json.loads(b["verdict"]) if b.get("verdict") else None} for (b,) in _ro(
        g, "MATCH (:Investigation {id: $id})-[:PRODUCED]->(b:Brief) OPTIONAL MATCH (b)-[:APPROVED_BY|REJECTED_BY]->(a:Approval) "
           "RETURN b {.id, .text, .citations, .status, .verdict, .thread, .created, .updated, decided_by: a.by, decided_at: a.at} "
           "ORDER BY b.created DESC", id=inv_id)]  # newest draft first; sent-back drafts stay for the record
    cands = [c for (c,) in _ro(g, "MATCH (:Investigation {id: $id})-[:PROPOSED]->(c:Candidate) "
                                  "RETURN c {.srid, .ntsb_num, .score, .evidence, .status} ORDER BY c.score DESC", id=inv_id)]
    return {**head[0][0], "steps": steps(g, inv_id), "tasks": board(g, inv_id), "briefs": briefs, "candidates": cands}


def proposals(g, status="proposed", limit=100):
    """Review queue: candidates by status, best first."""
    return [c for (c,) in _ro(g, "MATCH (c:Candidate) WHERE $status IS NULL OR c.status = $status "
                                 "RETURN c {.key, .srid, .ntsb_num, .score, .evidence, .status, .proposed_at, .decided_at, .decided_by} "
                                 "ORDER BY c.score DESC LIMIT $limit", status=status, limit=max(1, min(int(limit), 500)))]
