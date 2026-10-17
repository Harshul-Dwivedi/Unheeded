"""The Unheeded agents (docs/architecture.md): Orchestrator -> Scout -> Analyst -> Clerk, as a LangGraph graph.

Principle: the graph holds the facts, the LLM reasons, plain code holds authority.
  - Tools come only from MCP servers (ours, plus the sponsor's read-only query tool) and run as agent_ro.
  - Plain code makes the must-have calls (resolve, find_open_warnings), logs every call as a Step, moves Tasks,
    saves Candidates and the Brief. The LLM plans, picks extra tools within an allowlist and budget, and writes prose.
  - Facts the Clerk may use are tool results only; the approval gate (task 6) checks every cited id.

Run: uv run python -m unheeded.agents "Was the Nome crash warned about?" [--workspace demo]
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from operator import or_
from pathlib import Path
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError, field_validator

from unheeded import memory as M
from unheeded import queries as Q
from unheeded.config import llm
from unheeded.tools import GENERIC_PLACE

BACKEND = Path(__file__).resolve().parents[1]
# paused runs live here until a person decides; on disk, so a restart does not lose a pending approval
CHECKPOINT_DB = os.environ.get("CHECKPOINT_DB") or str(BACKEND / ".cache" / "checkpoints.sqlite")
ANALYST_BUDGET = 6  # extra tool calls the Analyst may choose
MIN_PROPOSAL_SCORE = 0.5  # below this a proposal is not worth a reviewer's time (e.g. one shared event type, score 0.1)
TOOL_RESULT_CHARS = 6000  # what the LLM sees per tool result (the Step log keeps everything)
ANALYST_TOOLS = {"status_on_date", "trace_chain", "get_letters", "find_candidates", "explain_path", "similar_cases",
                 "get_sources", "owner_backlog", "find_open_warnings", "prove_no_linked_warning",
                 "query_graph_readonly", "get_graph_schema"}  # last two: the sponsor's FalkorDB MCP server, ntsb only
SRID = re.compile(r"\bA-(\d{2})-(\d{1,3})\b")
NTSB_NUM = re.compile(r"\b[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]*\b")
REPORT = re.compile(r"\b[A-Z]{2,4}-\d{2}-\d{2}\b")
LETTER = re.compile(r"\bL\d+\b")

RULES = (
    "Rules: state only dates, statuses, numbers and ids that appear in the tool results you were given. "
    "A 'linked warning' means the NTSB repeated or reclassified the recommendation in that crash's own report, it was "
    "issued before the crash, and it was open on the crash date; 'linked' never means the crash would have been "
    "prevented. Use NTSB status wording, e.g. 'rated Open - Unacceptable Response by the NTSB'. Candidates from "
    "find_candidates are unverified proposals, never NTSB findings. Never name individuals."
)
PLANNER = ("ROLE: planner. Classify a question about US aviation safety. Reply with ONLY a JSON object, no prose: "
           '{"intent": "crash"|"recommendation"|"owner"|"unknown", "place": string|null, "ntsb_num": string|null, '
           '"year": integer|null, "srid": string|null, "acronym": string|null}. '
           "crash = a specific accident (give place and/or NTSB number, and year if stated); recommendation = a safety "
           "recommendation id like A-16-035; owner = what an organisation (FAA, EASA...) has open. Do not answer it.")
ANALYST = ("ROLE: analyst. You investigate with read-only tools over the NTSB graph. The facts already gathered are "
           f"in the first message. You may make up to {ANALYST_BUDGET} more tool calls that add evidence (status on the "
           "crash date, supersession chains, letters, unverified candidates, explanation paths). Dates are yyyymmdd "
           "integers. When you are done, reply in two sentences on what you checked, with no new facts. " + RULES)
CLERK = ("ROLE: clerk. Write a short brief answering the question from the facts JSON only. Cite every recommendation, "
         "report, crash and letter id in square brackets, e.g. [A-16-035] [AIR-26-05] [ANC25MA018]. Structure: one-line "
         "answer; the linked warnings with their status on the crash date; anything unverified, clearly labelled "
         "'Unverified proposals (not NTSB findings)'; one closing line: 'A linked warning does not establish that acting on "
         "it would have prevented the crash.' If there is no linked warning, say whether the crash has an NTSB report in the "
         "data (absence.reports): with no report there was nothing to search, so say that plainly; name any repeats in its "
         "report that fall outside the definition (absence.repeated_outside_definition) and that they are not linked "
         "warnings. Plain text, no markdown tables. " + RULES)


class Plan(BaseModel):
    intent: Literal["crash", "recommendation", "owner", "unknown"]
    place: str | None = Field(default=None, max_length=80)
    ntsb_num: str | None = None
    year: int | None = Field(default=None, ge=1960, le=2030)
    srid: str | None = None
    acronym: str | None = Field(default=None, pattern=r"^[A-Z]{2,10}$")

    @field_validator("srid")
    @classmethod
    def _srid(cls, v):
        if v is None:
            return v
        m = SRID.search(v.upper())
        if not m:
            raise ValueError("not a recommendation id")
        return f"A-{m.group(1)}-{int(m.group(2)):03d}"

    @field_validator("ntsb_num")
    @classmethod
    def _ntsb(cls, v):
        if v is None:
            return v
        if not NTSB_NUM.fullmatch(v.upper()):
            raise ValueError("not an NTSB number")
        return v.upper()


def plan_from_text(question):
    """Deterministic planner: the stub's brain and the fallback when an LLM reply does not validate."""
    q = question.strip()
    if m := SRID.search(q.upper()):
        return Plan(intent="recommendation", srid=m.group(0))
    year = int(y.group(0)) if (y := re.search(r"\b(19[6-9]\d|20[0-3]\d)\b", q)) else None
    if n := NTSB_NUM.search(q.upper()):
        return Plan(intent="crash", ntsb_num=n.group(0), year=year)
    if (a := re.search(r"\b(FAA|EASA|NWS|DOT|TSA)\b", q)) and re.search(r"\b(open|backlog|sitting|outstanding|pending|ignor)", q, re.I):
        return Plan(intent="owner", acronym=a.group(1))
    stop = {"was", "the", "were", "is", "did", "had", "has", "what", "which", "why", "how", "a", "an", "in", "of", "crash",
            "accident", "warned", "warning", "about", "ntsb", "already", "open", "near", "plane", "helicopter"}
    words = [w for w in re.findall(r"\b[A-Z][A-Za-z'’-]*[a-z]\b", q) if w.lower() not in stop]  # "McKinney", "O'Hare" stay whole
    return Plan(intent="crash", place=" ".join(words), year=year) if words else Plan(intent="unknown")


def ids_in(text):
    """Every recommendation, report, crash and letter id in a text (normalised)."""
    srids = {f"A-{y}-{int(n):03d}" for y, n in SRID.findall(text)}
    return srids | set(REPORT.findall(text)) | set(NTSB_NUM.findall(text)) | set(LETTER.findall(text))


def citations(text):
    """Ids the brief cites in [square brackets]. The approval gate (task 6) also checks every other id in the text."""
    return sorted(set().union(*(ids_in(c) for c in re.findall(r"\[([^\]]{1,40})\]", text))))


def _parse(out):
    """MCP tool output (str / content blocks / (content, artifact)) -> dict."""
    if isinstance(out, tuple):
        out = out[0]
    if isinstance(out, list):
        out = "".join(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in out)
    try:
        return json.loads(out)
    except (TypeError, ValueError):
        return {"error": str(out)[:500]}


def _shrink(x, depth=0):
    """Deterministic size cap (lists to 10 items, strings to 400 chars) so nothing is cut mid-JSON."""
    if isinstance(x, dict):
        return {k: _shrink(v, depth + 1) for k, v in x.items() if k not in ("queries", "ms")}
    if isinstance(x, list):
        return [_shrink(v, depth + 1) for v in x[:10]] + ([f"... {len(x) - 10} more"] if len(x) > 10 else [])
    if isinstance(x, str) and len(x) > 400:
        return x[:400] + "..."
    return x


def _brief_facts(facts):
    """What the Clerk sees: tool results only (plus the question's plan), size-capped."""
    return json.dumps(_shrink({k: v for k, v in facts.items() if k != "related_history"}), default=str)


class State(TypedDict, total=False):
    question: str
    workspace: str
    investigation: str
    status: str  # running | not_understood | not_found | drafted -> approved | rejected | blocked
    facts: Annotated[dict, or_]
    brief: dict
    decision: dict


class Agents:
    """One investigation run: MCP sessions + tools + the LangGraph graph."""

    def __init__(self, workspace, tools, model, on_step=None):
        self.ws, self.mem, self.tools, self.model, self.on_step = workspace, M.workspace(workspace), tools, model, on_step

    def effective(self, name, args):
        """Isolation overrides the model cannot change."""
        if name == "find_candidates":
            return {**args, "workspace": self.ws}  # this workspace's rules and seeds, never another's
        if name in ("query_graph_readonly", "get_graph_schema"):
            return {**args, "graphName": "ntsb"}  # agents only read the library graph, never a workspace
        return args

    async def call(self, inv, agent, name, args):
        """Every tool call goes through here: allowlist, isolation overrides, Step log, Cypher stripped for the LLM."""
        if name not in self.tools:
            return {"error": f"tool {name} not available"}
        args = self.effective(name, args)
        t = time.perf_counter()
        result = _parse(await self.tools[name].ainvoke(args))
        wall = round((time.perf_counter() - t) * 1000, 2)
        queries = result.pop("queries", None) if isinstance(result, dict) else None
        if name == "query_graph_readonly":  # the sponsor server returns rows only; the Cypher is what we sent
            queries = [args.get("query", "")]
        n = M.log_step(self.mem, inv, agent, name, cypher="\n;\n".join(queries) if queries else None,
                       result_count=_count(result), ms=result.get("ms", wall) if isinstance(result, dict) else wall,
                       args=args)
        if self.on_step:
            self.on_step({"n": n, "agent": agent, "tool": name, "args": args, "ms": result.get("ms") if isinstance(result, dict) else None,
                          "wall_ms": wall, "cypher": queries, "summary": _summary(name, result)})
        return result

    # --- nodes --------------------------------------------------------------------------------------------------------

    async def orchestrator(self, s: State):
        past = M.history(self.mem, s["question"], limit=3)  # before creating this one, so it doesn't find itself
        inv = M.start_investigation(self.mem, s["question"])
        reply = await self.model.ainvoke([SystemMessage(PLANNER), HumanMessage(s["question"])])
        try:
            plan, basis = Plan.model_validate_json(_json_text(reply.content)), "llm"
        except (ValidationError, ValueError):
            plan, basis = plan_from_text(s["question"]), "fallback"  # deterministic, logged
        n = M.log_step(self.mem, inv, "orchestrator", "plan", args={"plan": plan.model_dump(), "basis": basis})
        if self.on_step:
            self.on_step({"n": n, "agent": "orchestrator", "tool": "plan", "args": {"basis": basis}, "ms": None, "cypher": None,
                          "summary": f"{plan.intent}" + (f" · {past[0]['question'][:40]}… seen before" if past else "")})
        if plan.intent == "unknown":
            return {"investigation": inv, "status": "not_understood", "facts": {"plan": plan.model_dump(), "related_history": past}}
        M.create_task(self.mem, inv, "resolve", plan.model_dump())
        return {"investigation": inv, "status": "running", "facts": {"plan": plan.model_dump(), "related_history": past}}

    async def scout(self, s: State):
        inv = s["investigation"]
        task = M.claim_task(self.mem, "scout", kind="resolve", inv_id=inv)
        p = Plan(**task["payload"])
        facts = {}
        if p.intent == "crash":
            res = await self.call(inv, "scout", "resolve_accident", {"text": p.ntsb_num or p.place or "", "year": p.year, "limit": 10})
            matches = res.get("matches") or []
            if not p.ntsb_num:  # fail closed: answering about the wrong crash is worse than asking
                words = {w.lower() for w in re.findall(r"[A-Za-z]{2,}", p.place or "")} - GENERIC_PLACE
                hits = lambda m: len(words & {w.lower() for w in re.findall(r"[A-Za-z]{2,}", m.get("city") or "")})
                best = max((hits(m) for m in matches), default=0)
                if not best:
                    M.complete_task(self.mem, task["id"], "scout", {"found": False})
                    return {"status": "not_found", "facts": {"matches": [], "did_you_mean": matches[:3]}}
                matches = [m for m in matches if hits(m) == best] + [m for m in matches if hits(m) < best]
            if not matches:
                M.complete_task(self.mem, task["id"], "scout", {"found": False})
                return {"status": "not_found", "facts": {"matches": []}}
            facts["accident"], facts["other_matches"] = matches[0], matches[1:4]
            sim = await self.call(inv, "scout", "similar_cases", {"ntsb_num": matches[0]["ntsb_num"], "limit": 5})
            facts["similar"] = sim.get("similar", [])
        elif p.intent == "recommendation":
            facts["chain"] = await self.call(inv, "scout", "trace_chain", {"srid": p.srid})
        else:
            facts["backlog"] = await self.call(inv, "scout", "owner_backlog", {"acronym": p.acronym, "limit": 20})
        M.complete_task(self.mem, task["id"], "scout", {"found": True})
        M.create_task(self.mem, inv, "analyse", {"plan": p.model_dump(), "ntsb_num": facts.get("accident", {}).get("ntsb_num")})
        return {"facts": facts}

    async def analyst(self, s: State):
        inv = s["investigation"]
        task = M.claim_task(self.mem, "analyst", kind="analyse", inv_id=inv)
        p, facts = Plan(**task["payload"]["plan"]), {}
        if p.intent == "crash":  # the headline is never left to the model
            n = s["facts"]["accident"]["ntsb_num"]
            facts["open_warnings"] = (await self.call(inv, "analyst", "find_open_warnings", {"ntsb_num": n})).get("warnings", [])
            if not facts["open_warnings"]:
                facts["absence"] = await self.call(inv, "analyst", "prove_no_linked_warning", {"ntsb_num": n})
            if "query_graph_readonly" in self.tools:  # the headline again, through the sponsor's FalkorDB MCP server
                x = await self.call(inv, "analyst", "query_graph_readonly",
                                    {"query": " ".join((Q.LINKED + "RETURN count(DISTINCT r) AS linked").split()), "params": {"id": n}})
                got = ((x.get("data") or [{}])[0]).get("linked") if isinstance(x, dict) else None
                facts["crosscheck"] = {"server": "falkordb-mcp", "linked": got, "agrees": got == len(facts["open_warnings"])}
        if "get_graph_schema" in self.tools:  # what the graph holds, from the sponsor server (labels, edge types, topology)
            schema = await self.call(inv, "analyst", "get_graph_schema", {})
            facts["schema_labels"] = schema.get("nodeLabels") if isinstance(schema, dict) else None
        context = {"question": s["question"], "intent": p.intent, "accident": s["facts"].get("accident"), "srid": p.srid,
                   "acronym": p.acronym, **facts, "chain": s["facts"].get("chain"),
                   "backlog_counts": (s["facts"].get("backlog") or {}).get("counts")}
        allowed = [t for name, t in self.tools.items() if name in ANALYST_TOOLS]
        model = self.model.bind_tools(allowed)
        msgs = [SystemMessage(ANALYST), HumanMessage(json.dumps(context, default=str))]
        calls, used = [], 0
        while used < ANALYST_BUDGET:
            ai = await model.ainvoke(msgs)
            msgs.append(ai)
            if not ai.tool_calls:
                break
            for tc in ai.tool_calls:
                used += 1
                if tc["name"] not in ANALYST_TOOLS or used > ANALYST_BUDGET:
                    result = {"error": "not allowed" if tc["name"] not in ANALYST_TOOLS else "tool budget used up"}
                else:
                    result = await self.call(inv, "analyst", tc["name"], tc["args"])
                    calls.append({"tool": tc["name"], "args": self.effective(tc["name"], tc["args"]), "result": result})
                msgs.append(ToolMessage(json.dumps(result, default=str)[:TOOL_RESULT_CHARS], tool_call_id=tc["id"]))
        for c in calls:  # plain code saves proposals, and only from find_candidates output
            if c["tool"] == "find_candidates" and isinstance(c["result"], dict) and c["result"].get("verified") is False:
                strong = [x for x in c["result"]["candidates"] if x["score"] >= MIN_PROPOSAL_SCORE]
                if strong:
                    M.save_candidates(self.mem, inv, {**c["result"], "candidates": strong})
        M.complete_task(self.mem, task["id"], "analyst", {"tool_calls": len(calls)})
        M.create_task(self.mem, inv, "draft_brief", {"intent": p.intent})
        return {"facts": {**facts, "calls": calls, "analyst_note": _text(msgs[-1]) if isinstance(msgs[-1], AIMessage) else ""}}

    async def clerk(self, s: State):
        inv = s["investigation"]
        task = M.claim_task(self.mem, "clerk", kind="draft_brief", inv_id=inv)
        facts = s["facts"]
        if s.get("status") in ("not_understood", "not_found"):
            text = ("I could not tell which crash, recommendation or organisation the question is about."
                    if s["status"] == "not_understood" else
                    f"No crash in the graph matches \"{facts['plan'].get('ntsb_num') or facts['plan'].get('place')}\". "
                    "Try an NTSB number (e.g. ANC25MA018) or a place and year."
                    + ("" if not facts.get("did_you_mean") else " Did you mean: " + "; ".join(
                        f"{m['city']}, {m.get('state') or ''} {m['event_date']} [{m['ntsb_num']}]" for m in facts["did_you_mean"]) + "?"))
        else:
            ids = sorted({w["srid"] for w in facts.get("open_warnings", [])} | ({facts["plan"]["srid"]} if facts["plan"].get("srid") else set()))
            if ids:
                facts["sources"] = await self.call(inv, "clerk", "get_sources", {"ids": ids})
            msgs = [SystemMessage(CLERK), HumanMessage(_brief_facts(facts))]
            note = (task or {}).get("payload", {}).get("note") if task else None
            if note is not None and task.get("payload", {}).get("previous"):  # a redraft after "send back"
                msgs.append(HumanMessage(f"The reviewer sent your previous draft back with this note: {note!r}. "
                                         f"Previous draft:\n{s['brief']['text']}\nWrite the corrected brief."))
            text = _text(await self.model.ainvoke(msgs))
        cites = citations(text)
        brief_id = M.save_brief(self.mem, inv, text, cites)
        if task:
            M.complete_task(self.mem, task["id"], "clerk", {"brief": brief_id})
        M.finish_investigation(self.mem, inv, text.splitlines()[0][:300] if text else "")
        return {"status": "drafted" if s.get("status") == "running" else s.get("status"),
                "brief": {"id": brief_id, "text": text, "citations": cites, "status": "draft"}}

    async def gate(self, s: State, config):
        """Plain code, final authority: every claim in the brief checked against the graph."""
        from unheeded import gate as G
        b = s["brief"]
        subject = (s["facts"].get("accident") or {}).get("ntsb_num") if s.get("status") != "not_found" else None
        subject = subject or (s["facts"].get("plan") or {}).get("srid")
        t = time.perf_counter()
        v = G.check(b["text"], subject, require_claims=s.get("status") not in ("not_understood", "not_found"))
        status = "awaiting_approval" if v["passed"] else "blocked"
        M.update_brief(self.mem, b["id"], status, verdict=v, thread=config["configurable"]["thread_id"])
        queries = v.pop("queries", [])
        n = M.log_step(self.mem, s["investigation"], "gate", "check", result_count=len(v["blocked"]), cypher="\n;\n".join(queries),
                       ms=round((time.perf_counter() - t) * 1000, 2),
                       args={"passed": v["passed"], "claims_checked": v["claims_checked"], "blocked": v["blocked"][:10]})
        if self.on_step:
            self.on_step({"n": n, "agent": "gate", "tool": "check", "args": {"passed": v["passed"]}, "ms": None, "cypher": queries,
                          "summary": f"{v['claims_checked']} claims checked · " + ("passed" if v["passed"] else f"{len(v['blocked'])} blocked")})
        return {"brief": {**b, "status": status, "verdict": v}}

    def approval(self, s: State):
        """Pauses the run (LangGraph interrupt) until a person decides. Nothing before interrupt() may have side effects:
        the node re-runs from the top on resume."""
        b = s["brief"]
        d = interrupt({"investigation": s["investigation"], "brief_id": b["id"], "status": b["status"], "text": b["text"],
                       "blocked": b["verdict"]["blocked"], "warnings": b["verdict"]["warnings"],
                       "allowed": (["approve"] if b["status"] == "awaiting_approval" else []) + ["edit", "send_back", "reject"]})
        action, reviewer = d.get("action"), d.get("reviewer") or "reviewer"
        if action == "send_back":  # the Clerk redrafts with the reviewer's note; the old draft stays, marked sent_back
            M.update_brief(self.mem, b["id"], "sent_back")
            M.create_task(self.mem, s["investigation"], "draft_brief", {"note": d.get("note") or "", "previous": b["id"]})
            return {"brief": {**b, "status": "sent_back"}, "decision": d}
        if action == "edit" and d.get("text"):  # back through the gate
            M.update_brief(self.mem, b["id"], "draft", text=d["text"], citations=citations(d["text"]))
            return {"brief": {**b, "text": d["text"], "citations": citations(d["text"]), "status": "draft"}, "decision": d}
        if action == "approve":
            ok = M.decide_brief(self.mem, b["id"], "approved", reviewer, d.get("note"))  # refused unless the gate passed
            return {"status": "approved" if ok else "blocked", "brief": {**b, "status": "approved" if ok else b["status"]},
                    "decision": {**d, "accepted": ok}}
        M.decide_brief(self.mem, b["id"], "rejected", reviewer, d.get("note"))
        return {"status": "rejected", "brief": {**b, "status": "rejected"}, "decision": d}

    def graph(self, saver):
        g = StateGraph(State)
        for name in ("orchestrator", "scout", "analyst", "clerk", "gate", "approval"):
            g.add_node(name, getattr(self, name))
        g.add_edge(START, "orchestrator")
        g.add_conditional_edges("orchestrator", lambda s: "clerk" if s["status"] != "running" else "scout", ["scout", "clerk"])
        g.add_conditional_edges("scout", lambda s: "clerk" if s.get("status") == "not_found" else "analyst", ["analyst", "clerk"])
        g.add_edge("analyst", "clerk")
        g.add_edge("clerk", "gate")
        g.add_edge("gate", "approval")
        g.add_conditional_edges("approval", lambda s: {"draft": "gate", "sent_back": "clerk"}.get(s["brief"]["status"], END), ["gate", "clerk", END])
        return g.compile(checkpointer=saver)


def _json_text(content):
    t = _text(content) if not isinstance(content, str) else content
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object")
    return m.group(0)


def _text(msg):
    c = msg.content if hasattr(msg, "content") else msg
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return c or ""


def _summary(name, result):
    """One line for the streaming view, from the tool result only."""
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return "error"
    n = _count(result)
    return {"resolve_accident": lambda: (result["matches"][0]["ntsb_num"] if result.get("matches") else "no match"),
            "find_open_warnings": lambda: f"{n} linked warning{'s' * (n != 1)}",
            "prove_no_linked_warning": lambda: ("linked warnings exist" if not result.get("no_linked_warning") else
                                                "no NTSB report in the data: nothing to search" if not result.get("has_report") else
                                                f"no linked warning in {', '.join(result['reports'])}: proven"),
            "find_candidates": lambda: f"{n} unverified proposal{'s' * (n != 1)}",
            "query_graph_readonly": lambda: (", ".join(f"{k} {v}" for k, v in result["data"][0].items()) if len(result.get("data") or []) == 1
                                             else f"{len(result.get('data') or [])} rows") + " · via FalkorDB's MCP server",
            "get_graph_schema": lambda: f"{len(result.get('nodeLabels') or [])} labels, {len(result.get('relationshipTypes') or [])} edge types",
            "similar_cases": lambda: f"{n} similar crashes",
            "trace_chain": lambda: f"{len(result.get('repeats') or [])} repeats in later reports",
            "explain_path": lambda: f"{len((result.get('path') or {}).get('edges') or [])}-hop path" if result.get("path") else "no path",
            "get_sources": lambda: f"{len(result.get('recommendations') or [])} letters, {len(result.get('reports') or [])} reports",
            "get_letters": lambda: f"{n} letters", "owner_backlog": lambda: f"{result.get('total_open', 0)} open",
            "status_on_date": lambda: (result.get("status") or {}).get("code") or "not yet issued"}.get(name, lambda: f"{n} rows" if n is not None else "done")()


def _count(result):
    if not isinstance(result, dict):
        return None
    for k in ("warnings", "matches", "candidates", "similar", "open", "letters", "reached", "superseded_by"):
        if isinstance(result.get(k), list):
            return len(result[k])
    return None


def connections():
    conns = {"unheeded": {"transport": "stdio", "command": sys.executable, "args": ["-m", "unheeded.mcp_server"],
                          "cwd": str(BACKEND), "env": dict(os.environ)}}
    if os.environ.get("MCP_API_KEY") and os.environ.get("USE_FALKORDB_MCP", "1") == "1":
        conns["falkordb"] = {"transport": "streamable_http", "url": os.environ.get("FALKORDB_MCP_URL", "http://127.0.0.1:8080/mcp"),
                             "headers": {"Authorization": f"Bearer {os.environ['MCP_API_KEY']}"}}
    return conns


def _finish(agents, state, thread):
    state["thread_id"] = thread
    state["paused"] = bool(state.get("__interrupt__"))
    state["steps"] = M.steps(agents.mem, state["investigation"])
    state["board"] = M.board(agents.mem, state["investigation"])
    return state


@asynccontextmanager
async def checkpointer():
    Path(CHECKPOINT_DB).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as saver:
        yield saver


async def pending(thread_id):
    """True if this run is paused at the approval step (survives restarts)."""
    async with checkpointer() as saver:
        snap = await Agents(thread_id.split(":")[0], {}, None).graph(saver).aget_state({"configurable": {"thread_id": thread_id}})
        return bool(snap.next)


async def load_tools(stack, client):
    tools = {}
    for server in client.connections:
        try:
            session = await stack.enter_async_context(client.session(server))
        except Exception as e:  # the sponsor server is optional; ours is not
            if server == "unheeded":
                raise
            print(f"[agents] {server} MCP server unavailable, continuing without it: {e}", file=sys.stderr)
            continue
        for t in await load_mcp_tools(session):
            if server == "unheeded" or t.name in ("query_graph_readonly", "get_graph_schema"):  # sponsor: read tools only
                tools[t.name] = t
    return tools


async def resume(thread_id, decision, model=None, on_step=None):
    """Continue a paused run with a person's decision:
    {"action": "approve"|"edit"|"send_back"|"reject", "reviewer", "text"? (edit), "note"? (send_back / any)}."""
    async with AsyncExitStack() as stack:
        redraft = decision.get("action") == "send_back"  # only a redraft needs the Clerk's tools and model
        tools = await load_tools(stack, MultiServerMCPClient(connections())) if redraft else {}
        agents = Agents(thread_id.split(":")[0], tools, (model or llm()) if redraft else None, on_step)
        saver = await stack.enter_async_context(checkpointer())
        graph = agents.graph(saver)
        config = {"configurable": {"thread_id": thread_id}}
        if not (await graph.aget_state(config)).next:
            raise LookupError(f"no paused run {thread_id}")  # fail closed: never start a new run from a decision
        state = await graph.ainvoke(Command(resume=decision), config)
        return _finish(agents, state, thread_id)


async def run(question, workspace="demo", on_step=None, model=None, decision=None, thread_id=None):
    """One investigation, up to the approval pause (or through it when `decision` is given)."""
    async with AsyncExitStack() as stack:
        tools = await load_tools(stack, MultiServerMCPClient(connections()))
        agents = Agents(workspace, tools, model or llm(), on_step)
        thread = thread_id or f"{workspace}:{time.time_ns()}"
        saver = await stack.enter_async_context(checkpointer())
        state = await agents.graph(saver).ainvoke({"question": question, "workspace": workspace}, {"configurable": {"thread_id": thread}})
        state = _finish(agents, state, thread)
    if decision and state["paused"]:
        return await resume(thread, decision, model=model)
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--workspace", default="demo")
    ap.add_argument("--decision", choices=["approve", "reject"], help="decide at the approval pause (default: ask, if a terminal)")
    a = ap.parse_args()
    show = lambda st: print(f"  step {st['n']:>2}  {st['agent']:<8} {st['tool']:<24} {st['ms']} ms", file=sys.stderr)
    state = asyncio.run(run(a.question, a.workspace, on_step=show))
    b = state["brief"]
    print(f"\n[{state['status']} / brief {b['status']}] investigation {state['investigation']} in ws_{a.workspace}\n")
    print(b["text"])
    print("\ncitations:", ", ".join(b["citations"]))
    v = b.get("verdict") or {}
    print(f"\ngate: {'PASSED' if v.get('passed') else 'BLOCKED'}, {v.get('claims_checked', 0)} claims checked")
    for x in v.get("blocked", []):
        print(f"  BLOCKED [{x['check']}] {x['reason']}")
    for w in v.get("warnings", [])[:3]:
        print(f"  warning: {w['reason']}")
    if state["paused"]:
        choice = a.decision or (input("approve / reject? ").strip().lower() if sys.stdin.isatty() else None)
        if choice in ("approve", "reject"):
            state = asyncio.run(resume(state["thread_id"], {"action": choice, "reviewer": "cli"}))
            print(f"\n-> brief {state['brief']['status']}")
        else:
            print(f"\npaused for approval (thread {state['thread_id']})")


if __name__ == "__main__":
    main()
