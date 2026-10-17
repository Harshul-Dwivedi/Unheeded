"""Workspace memory against a live FalkorDB (needs `make up`, .env and, for some checks, the loaded `ntsb` graph)."""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from redis.exceptions import NoPermissionError

from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect

NOME = "ANC25MA018"


@pytest.fixture(scope="module")
def ntsb():
    try:
        db = connect("agent_ro")
        if "ntsb" not in db.list_graphs():
            pytest.skip("graph not loaded")
        M.workspaces()  # ws_writer reachable
    except Exception as e:
        pytest.skip(f"FalkorDB / users not available: {e}")
    return db.select_graph("ntsb")


@pytest.fixture
def ws(ntsb):
    name = f"test_{uuid.uuid4().hex[:8]}"
    yield name, M.workspace(name)
    M.drop_workspace(name)


def test_isolation_enforced_by_falkordb(ws):
    name, g = ws
    with pytest.raises(NoPermissionError):
        M._db().select_graph("ntsb").query("CREATE (:X)")  # ws_writer cannot touch ntsb
    with pytest.raises(NoPermissionError):
        connect("agent_ro").select_graph(f"ws_{name}").query("CREATE (:X)")  # agents' read-only user cannot write memory
    with pytest.raises(ValueError):
        M.workspace("ntsb} OR 1=1")


def test_step_chain_append_and_idempotent(ws):
    _, g = ws
    inv = M.start_investigation(g, "Was the Nome crash warned about?")
    assert [M.log_step(g, inv, "analyst", t, ms=0.1) for t in ("a", "b", "c")] == [1, 2, 3]
    assert M.log_step(g, inv, "analyst", "d", step_id="s4") == 4
    assert M.log_step(g, inv, "analyst", "d", step_id="s4") is None  # retry: not appended twice
    assert [s["tool"] for s in M.steps(g, inv)] == ["a", "b", "c", "d"]


def test_claim_race_one_winner(ws):
    _, g = ws
    inv = M.start_investigation(g, "race")
    M.create_task(g, inv, "check_warnings", {"ntsb_num": NOME}, task_id="t1")
    M.create_task(g, inv, "check_warnings", task_id="t1")  # MERGE: idempotent create
    with ThreadPoolExecutor(20) as pool:
        got = list(pool.map(lambda i: M.claim_task(g, f"agent{i}"), range(20)))
    winners = [x for x in got if x]
    assert len(winners) == 1 and winners[0]["id"] == "t1" and winners[0]["attempts"] == 1
    assert len(M.board(g)) == 1


def test_lease_expiry_and_completion(ws):
    _, g = ws
    inv = M.start_investigation(g, "lease")
    M.create_task(g, inv, "draft_brief", task_id="t1", now=1000)
    assert M.claim_task(g, "clerk", lease_ms=100, now=1000)["id"] == "t1"
    assert M.claim_task(g, "clerk2", now=1050) is None                    # lease still held
    assert M.claim_task(g, "clerk2", now=1200)["attempts"] == 2           # expired: taken over
    assert M.complete_task(g, "t1", "clerk", now=1201) is False           # old holder lost it: fail closed
    assert M.complete_task(g, "t1", "clerk2", now=1201) is True
    assert M.board(g)[0]["status"] == "done"


def test_candidates_feedback_rules_seeds(ws, ntsb):
    _, g = ws
    inv = M.start_investigation(g, "Nome candidates")
    res = T.find_candidates(ntsb, ntsb_num=NOME, limit=5)
    with pytest.raises(ValueError):
        M.save_candidates(g, inv, {"candidates": []})  # only find_candidates output is accepted
    assert M.save_candidates(g, inv, res) == 5 and M.save_candidates(g, inv, res) == 5  # MERGE: no duplicates
    a, b = res["candidates"][0]["id"], res["candidates"][1]["id"]
    t0 = M.now_ms()
    assert M.record_feedback(g, a, NOME, "confirmed", "reviewer-1")
    assert not M.record_feedback(g, a, NOME, "rejected", "reviewer-2")  # already decided: counted once
    assert M.record_feedback(g, b, NOME, "rejected", "reviewer-1")
    assert M.belief_on(g, a, NOME, t0 - 3_600_000) is None  # before it was proposed
    assert M.belief_on(g, a, NOME, M.now_ms()) == "confirmed"
    assert M.seeds(g) == [(a, NOME)]
    r = M.rules(g)
    assert r["decided"] == 2 and r["confirmed"] == 1
    assert sum(1 for x in g.query("MATCH (c:Candidate) RETURN c").result_set) == 5


def test_undo_feedback_with_audit(ws, ntsb):
    _, g = ws
    inv = M.start_investigation(g, "undo")
    res = T.find_candidates(ntsb, ntsb_num=NOME, limit=2)
    M.save_candidates(g, inv, res)
    a = res["candidates"][0]["id"]
    assert not M.undo_feedback(g, a, NOME, "r")  # nothing decided yet
    assert M.record_feedback(g, a, NOME, "confirmed", "r1")
    hits = lambda: {s: c["hits"] for s, c in M.rules(g)["signals"].items()}
    assert set(hits().values()) == {1}
    assert M.undo_feedback(g, a, NOME, "r2")
    assert set(hits().values()) == {0} and M.seeds(g) == [] and M.rules(g)["decided"] == 0
    assert g.ro_query("MATCH (v:Revision {from_status: 'confirmed', decided_by: 'r1', undone_by: 'r2'})-[:OF]->(:Candidate) RETURN count(v)").result_set == [[1]]
    assert M.record_feedback(g, a, NOME, "rejected", "r2")  # can be decided again


def test_fit_weights():
    # findings separate confirmed from rejected; text does not: findings should gain relative to text
    rows = [({"text": 0.6, "findings": 4}, True)] * 8 + [({"text": 0.6, "findings": 0}, False)] * 30 + [({"text": 0.7}, False)] * 10
    w = M.fit_weights(rows)
    assert w["findings"] / w["text"] > T.DEFAULT_WEIGHTS["findings"] / T.DEFAULT_WEIGHTS["text"]
    assert w["seed"] == T.DEFAULT_WEIGHTS["seed"] and w["family"] == T.DEFAULT_WEIGHTS["family"]  # never varied: prior kept
    assert all(v >= 0 for v in w.values())
    assert M.fit_weights(rows) == w  # deterministic


def test_seeds_change_ranking(ntsb):
    cold = T.find_candidates(ntsb, ntsb_num=NOME, limit=100)
    warm = T.find_candidates(ntsb, ntsb_num=NOME, limit=100, seeds=[("A-21-038", "ANC20MA010")])
    rank = lambda r: [c["id"] for c in r["candidates"]].index("A-21-038")
    assert rank(warm) < rank(cold) and warm["seeds_used"] == 1


def test_history_and_watch_alerts(ws, ntsb):
    _, g = ws
    M.start_investigation(g, "Helicopter tour crashes in Hawaii")
    M.start_investigation(g, "Was the Nome crash warned about?")
    assert [h["question"] for h in M.history(g, "helicopter")] == ["Helicopter tour crashes in Hawaii"]
    M.add_watch(g, "Organization", "FAA")
    M.add_watch(g, "Accident", NOME)
    M.add_watch(g, "Recommendation", "A-99-999")  # not reached
    with pytest.raises(ValueError):
        M.add_watch(g, "Letter", "L1")
    reach = T.alert_reach(ntsb, "A-16-035")["reached"]
    hits = M.raise_alerts(g, "A-16-035", "status changed", reach)
    assert sorted(h["target_id"] for h in hits) == ["ANC25MA018", "FAA"]
