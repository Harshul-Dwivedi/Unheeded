"""The LangGraph agents end to end with the stub model: real graph, real MCP servers, real workspace memory."""
import asyncio
import json
import uuid

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from unheeded import agents as A
from unheeded import memory as M
from unheeded.config import connect
from unheeded.stub_llm import StubChat

NOME_LINKED = ["A-13-012", "A-13-013", "A-16-034", "A-16-035", "A-22-021", "A-22-022", "A-24-017", "A-24-018"]


@pytest.fixture(scope="module", autouse=True)
def needs_graph():
    try:
        if "ntsb" not in connect("agent_ro").list_graphs():
            pytest.skip("graph not loaded")
        M.workspaces()
    except Exception as e:
        pytest.skip(f"FalkorDB / users not available: {e}")


@pytest.fixture
def ws():
    name = f"test_{uuid.uuid4().hex[:8]}"
    yield name
    M.drop_workspace(name)


def run(question, ws, model=None, decision=None):
    return asyncio.run(A.run(question, ws, model=model or StubChat(), decision=decision))


def test_nome_end_to_end(ws):
    s = run("Was the Nome crash warned about?", ws)
    assert s["status"] == "drafted" and s["paused"] and s["brief"]["status"] == "awaiting_approval"
    assert s["brief"]["verdict"]["passed"] and s["brief"]["verdict"]["claims_checked"] >= 30
    assert [w["srid"] for w in s["facts"]["open_warnings"]] == NOME_LINKED
    assert set(NOME_LINKED + ["ANC25MA018"]) <= set(s["brief"]["citations"])
    assert "A linked warning does not establish that acting on it would have prevented the crash." in s["brief"]["text"]
    assert [(t["kind"], t["status"]) for t in s["board"]] == [("resolve", "done"), ("analyse", "done"), ("draft_brief", "done")]
    tools = [st["tool"] for st in s["steps"]]
    assert tools[:4] == ["plan", "resolve_accident", "similar_cases", "find_open_warnings"] and "find_candidates" in tools
    assert all(st["cypher"] for st in s["steps"] if st["tool"] not in ("plan", "get_graph_schema"))  # exact Cypher per call
    g = M.workspace(ws)
    saved = {r[0] for r in g.ro_query("MATCH (c:Candidate) RETURN c.srid").result_set}
    assert saved and not saved & set(NOME_LINKED)  # proposals never repeat official links
    assert g.ro_query("MATCH (b:Brief {status: 'awaiting_approval'}) RETURN count(b)").result_set == [[1]]
    done = asyncio.run(A.resume(s["thread_id"], {"action": "approve", "reviewer": "tester"}))
    assert done["status"] == "approved" and not done["paused"]
    assert g.ro_query("MATCH (b:Brief {status: 'approved'})-[:APPROVED_BY]->(a:Approval {by: 'tester'}) RETURN count(b)").result_set == [[1]]


def test_generic_place_words_do_not_pick_a_crash(ws):
    s = run("Was the Bering Air crash near Nome warned about?", ws)  # "Air" also matches "Hida Air Park"
    assert s["facts"]["accident"]["ntsb_num"] == "ANC25MA018"


def test_togiak_trap(ws):
    s = run("Was the Togiak crash in 2016 warned about?", ws)
    assert s["facts"]["accident"]["ntsb_num"] == "ANC17MA001"
    linked = [w["srid"] for w in s["facts"]["open_warnings"]]
    assert linked and "A-16-035" not in linked  # issued after the crash


def test_owner_and_recommendation(ws):
    s = run("What does the FAA still have open?", ws)
    assert s["facts"]["backlog"]["total_open"] == 228 and "228 open" in s["brief"]["text"]
    s = run("What happened with A-16-35?", ws)  # short form normalised
    assert s["facts"]["plan"]["srid"] == "A-16-035" and "[AIR-26-05]" in s["brief"]["text"]


def test_not_understood_and_not_found(ws):
    assert run("hello there", ws)["status"] == "not_understood"
    s = run("Was the Atlantis crash in 2022 warned about?", ws)
    assert s["status"] == "not_found" and "No crash in the graph matches" in s["brief"]["text"]


class Scripted(BaseChatModel):
    """Planner reply, then analyst tool calls from `calls`, then `brief` as the clerk's text."""
    calls: list = []
    turn: int = 0
    brief: str = "done"

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kw):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        role = messages[0].content.split()[1].rstrip(".")
        if role == "planner":
            msg = AIMessage(content="Sure! " + json.dumps({"intent": "crash", "place": "Nome", "year": 2025}))
        elif role == "analyst" and self.turn < len(self.calls):
            name, args = self.calls[self.turn]
            self.turn += 1
            msg = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{self.turn}", "type": "tool_call"}])
        elif role == "clerk":
            msg = AIMessage(content=self.brief)
        else:
            msg = AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def test_allowlist_isolation_and_budget(ws):
    other = f"test_{uuid.uuid4().hex[:8]}"
    M.workspace(other)
    try:
        calls = [("delete_graph", {"graphName": "ntsb", "confirmDelete": True}),          # not on the allowlist
                 ("find_candidates", {"ntsb_num": "ANC25MA018", "workspace": other}),      # workspace is overridden
                 ("query_graph_readonly", {"graphName": f"ws_{other}", "query": "MATCH (n) RETURN count(n)"})] \
            + [("status_on_date", {"srid": "A-16-035", "date": 20250206})] * 10           # over budget
        s = run("Nome?", ws, model=Scripted(calls=calls, brief="[A-16-035] is a linked warning for [ANC25MA018]."))
        assert s["status"] == "drafted" and s["brief"]["status"] == "awaiting_approval"  # planner JSON inside prose still parsed
        steps = [st for st in s["steps"] if st["agent"] == "analyst"]
        assert "delete_graph" not in {st["tool"] for st in steps}
        cand = next(st for st in M.steps(M.workspace(ws), s["investigation"]) if st["tool"] == "find_candidates")
        logged = json.loads(M.workspace(ws).ro_query("MATCH (st:Step {n: $n})-[:IN]->(:Investigation {id: $i}) RETURN st.args",
                                                     {"n": cand["n"], "i": s["investigation"]}).result_set[0][0])
        assert logged["workspace"] == ws
        q = next((c for c in s["facts"]["calls"] if c["tool"] == "query_graph_readonly"), None)
        if q:  # sponsor server running: forced onto the library graph, schema read, headline cross-checked through it
            assert q["args"]["graphName"] == "ntsb"
            assert s["facts"]["crosscheck"] == {"server": "falkordb-mcp", "linked": 8, "agrees": True}
            assert "Recommendation" in s["facts"]["schema_labels"]
        plain = lambda st: st["tool"] in ("find_open_warnings", "get_graph_schema") or (  # code's own calls, not the model's
            st["tool"] == "query_graph_readonly" and json.loads(st["args"])["query"].startswith("MATCH (r:Recommendation)"))
        extra = sum(1 for st in steps if not plain(st))
        assert extra <= A.ANALYST_BUDGET
        assert "ntsb" in connect("agent_ro").list_graphs()
    finally:
        M.drop_workspace(other)


def test_plan_validation():
    assert A.Plan(intent="recommendation", srid="a-16-35").srid == "A-16-035"
    with pytest.raises(ValueError):
        A.Plan(intent="recommendation", srid="DROP GRAPH")
    with pytest.raises(ValueError):
        A.Plan(intent="owner", acronym="faa; MATCH")
    assert A.plan_from_text("Was the McKinney, Texas crash in 2024 warned about?").place == "McKinney Texas"
    assert A.plan_from_text("Was the O'Hare crash warned about?").place == "O'Hare"
    assert A.plan_from_text("Was the Kekaha Hawaii crash in 2019 warned about?").model_dump() == {
        "intent": "crash", "place": "Kekaha Hawaii", "ntsb_num": None, "year": 2019, "srid": None, "acronym": None}
    assert A.citations("see [A-16-35], [AIR-26-05], [ANC25MA018] [L1591] and e.g. CEN16MA036") == ["A-16-035", "AIR-26-05", "ANC25MA018", "L1591"]
    assert A.ids_in("e.g. CEN16MA036 and A-16-35") == {"CEN16MA036", "A-16-035"}


def test_person_checks_overlap():
    from unheeded import gate as G
    assert G.check("From Billy Nolen the response came.")["blocked"][0]["check"] == "person"  # pair after a capitalised word
    assert G.scrub_names("From Billy Nolen, Acting Administrator, Federal Aviation Administration: The FAA agrees.") == "The FAA agrees."
    assert G.scrub_names("From Aviation Accident Report AAR-20-01, PB2020-101001: text").startswith("From Aviation Accident Report")


def test_gate_blocks_then_edit_reapprove(ws):
    bad = ("Yes: [A-21-049] and [A-16-035] were linked warnings for [ANC25MA018].\n"
           "Acting on them would have prevented the crash. Captain Robert Hale had reported icing.")
    s = run("Nome?", ws, model=Scripted(brief=bad))
    v = s["brief"]["verdict"]
    assert s["paused"] and s["brief"]["status"] == "blocked"
    assert {b["check"] for b in v["blocked"]} == {"link", "prevent", "person"}
    mention = next(b for b in v["blocked"] if b["check"] == "link")
    assert "A-21-049" in mention["reason"] and mention["weaker_path"] == "MENTIONED_IN"
    refused = asyncio.run(A.resume(s["thread_id"], {"action": "approve", "reviewer": "tester"}))
    assert refused["status"] == "blocked" and refused["decision"]["accepted"] is False  # fail closed
    s = run("Nome?", ws, model=Scripted(brief=bad))
    edited = asyncio.run(A.resume(s["thread_id"], {"action": "edit", "reviewer": "tester",
                                                   "text": "[A-16-035] was a linked warning for [ANC25MA018]."}))
    assert edited["paused"] and edited["brief"]["status"] == "awaiting_approval"  # re-gated, paused again
    done = asyncio.run(A.resume(s["thread_id"], {"action": "approve", "reviewer": "tester"}))
    assert done["status"] == "approved"
    s = run("Nome?", ws, model=Scripted(brief="[A-16-035] was a linked warning for [ANC25MA018]."), decision={"action": "reject"})
    assert s["status"] == "rejected"


def test_send_back_redrafts(ws):
    model = Scripted(brief="[A-16-035] was a linked warning for [ANC25MA018].")
    s = run("Nome?", ws, model=model)
    first = s["brief"]["id"]
    s = asyncio.run(A.resume(s["thread_id"], {"action": "send_back", "reviewer": "tester", "note": "add the report number"}, model=model))
    assert s["paused"] and s["brief"]["id"] != first and s["brief"]["status"] == "awaiting_approval"  # new draft, re-gated
    briefs = M.investigation(M.workspace(ws), s["investigation"])["briefs"]
    assert [b["status"] for b in briefs] == ["awaiting_approval", "sent_back"]  # newest first; the old draft is kept
    assert [t["kind"] for t in s["board"]].count("draft_brief") == 2
    done = asyncio.run(A.resume(s["thread_id"], {"action": "approve", "reviewer": "tester"}))
    assert done["status"] == "approved"


def test_gate_unverified_section_and_empty_brief():
    from unheeded import gate as G
    sneaky = G.check("[A-16-035] is a linked warning for this crash; candidates aside.", "ANC15MA041")
    assert [b["check"] for b in sneaky["blocked"]] == ["link"]  # a keyword alone no longer switches the check off
    section = G.check("Yes.\nUnverified proposals (not NTSB findings): [A-16-035] (text 0.6).\n- [A-13-013] (findings 2)", "ANC25MA018")
    assert not [b for b in section["blocked"] if b["check"] == "link"]
    assert [b["check"] for b in G.check("Nothing to see here.", "ANC15MA041")["blocked"]] == ["empty"]
    assert G.check("No crash matches.", require_claims=False)["passed"]
    own = G.check("The report did repeat [A-16-036] (issued after the crash), so it does not count as a linked warning.", "ANC15MA041")
    assert own["passed"] and own["claims_checked"] == 1
    lie = G.check("[A-16-035] is not a linked warning for this crash.", "ANC25MA018")
    assert [b["check"] for b in lie["blocked"]] == ["link"]


def test_scrub_names_shapes():
    from unheeded import gate as G
    for raw, want in (("Chair Homendy said", "Chair [name] said"), ("MR. JAMES HENDERSON wrote", "MR. [name] wrote"),
                      ("Captain McDonald flew", "Captain [name] flew"), ("Mike Whitaker wrote", "[name] wrote"),
                      ("Ronald Reagan Washington National Airport", "Ronald Reagan Washington National Airport"),
                      ("Member States agree", "Member States agree"),
                      ("-From Jean-Brice DUMONT, Director & Head: The text.", "The text.")):
        assert G.scrub_names(raw) == want, raw
