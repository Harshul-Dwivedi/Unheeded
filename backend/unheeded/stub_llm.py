"""Offline, deterministic stand-in for the LLM (LLM_PROVIDER=stub).

It is not intelligent: it follows a fixed policy per role so tests, evals and keyless demos exercise the real graph,
MCP tools, memory and LangGraph flow end to end. Every fact it writes is copied from tool results, which makes it a
useful baseline for the approval gate too.
"""
import datetime
import json
import re

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def fmt(d):
    """20250206 -> '6 Feb 2025'."""
    if not d:
        return "an unknown date"
    x = datetime.date(d // 10000, d // 100 % 100, d % 100)
    return f"{x.day} {x.strftime('%b %Y')}"


class StubChat(BaseChatModel):
    tool_names: list[str] = []

    @property
    def _llm_type(self):
        return "unheeded-stub"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tool_names": [getattr(t, "name", t) for t in tools]})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        role = re.search(r"ROLE: (\w+)", messages[0].content).group(1)
        return ChatResult(generations=[ChatGeneration(message=getattr(self, f"_{role}")(messages))])

    def _planner(self, messages):
        from unheeded.agents import plan_from_text  # local: agents imports config, which imports this lazily
        return AIMessage(content=plan_from_text(messages[1].content).model_dump_json())

    def _analyst(self, messages):
        ctx = json.loads(messages[1].content)
        made = [tc["name"] for m in messages if isinstance(m, AIMessage) for tc in m.tool_calls]
        want = []
        if ctx["intent"] == "crash":
            a, ws = ctx["accident"], ctx.get("open_warnings") or []
            if ws:
                top = max(ws, key=lambda w: w.get("times_reiterated") or 0)
                want += [("trace_chain", {"srid": top["srid"]}), ("explain_path", {"srid": ws[0]["srid"], "ntsb_num": a["ntsb_num"]})]
            want.append(("find_candidates", {"ntsb_num": a["ntsb_num"], "limit": 5}))
        elif ctx["intent"] == "recommendation":
            want += [("get_letters", {"srid": ctx["srid"], "limit": 10}), ("find_candidates", {"srid": ctx["srid"], "limit": 5})]
        todo = [(n, a) for n, a in want if n not in made and n in self.tool_names]
        if todo:  # one call per turn
            name, args = todo[0]
            return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"stub_{len(made)}", "type": "tool_call"}])
        return AIMessage(content=f"Checked: {', '.join(made) or 'the facts already gathered'}. No further calls needed.")

    def _clerk(self, messages):
        f = json.loads(messages[1].content)
        p, out = f["plan"], []
        if p["intent"] == "crash":
            a = f["accident"]
            date = fmt(a.get("event_date"))
            where = ", ".join(x for x in (a.get("city"), a.get("state")) if x)
            crash = f"the {where} crash on {date} ({a.get('fatal') or 0} killed) [{a['ntsb_num']}]"
            ws, reports = f.get("open_warnings") or [], " ".join(f"[{r}]" for r in a.get("reports") or [])
            if ws:
                out.append(f"Yes. In its report on {crash} {reports}, the NTSB repeated or reclassified {len(ws)} safety "
                           f"recommendations that were issued before the crash and still open on {date}:")
                for w in ws:
                    since = f" since {fmt(w['status_since'])}" if w.get("status_since") else ""
                    out.append(f"- [{w['srid']}] issued {fmt(w['issued'])}; on the crash date rated {w['status_on_crash_date']} "
                               f"by the NTSB{since}. Current status: {w['current_status']}.")
            elif not (f.get("absence") or {}).get("has_report"):
                out.append(f"No linked warning for {crash}, but the NTSB data holds no report for this crash, so there was "
                           f"nothing to search. This is not evidence either way.")
            else:
                ab = f["absence"]
                out.append(f"No. In its report on {crash} {' '.join(f'[{r}]' for r in ab['reports'])}, the NTSB repeated or "
                           f"reclassified no recommendation that was issued before the crash and still open on {date}.")
                if ab.get("repeated_outside_definition"):
                    out.append("The report did repeat " + "; ".join(f"[{x['srid']}] ({x['why']})" for x in ab["repeated_outside_definition"])
                               + ", so it does not count as a linked warning.")
        elif p["intent"] == "recommendation":
            c = f.get("chain") or {}
            out.append(f"[{p['srid']}]: the NTSB repeated, reclassified or mentioned it in {len(c.get('repeats') or [])} later reports.")
            for r in (c.get("repeats") or [])[:8]:
                if isinstance(r, dict):
                    crashes = " ".join(f"[{x}]" for x in r.get("accidents") or [])
                    out.append(f"- {r['how'].replace('_IN', '').lower()} in [{r['report']}] on {fmt(r['date'])} {crashes}".rstrip())
            for key, label in (("superseded_by", "Superseded by"), ("supersedes", "Supersedes")):
                if c.get(key):
                    out.append(f"{label}: " + ", ".join(f"[{x['srid']}]" for x in c[key] if isinstance(x, dict)))
        elif p["intent"] == "owner":
            b = f.get("backlog") or {}
            counts = ", ".join(f"{c['n']} rated {c['label']}" for c in b.get("counts") or [] if isinstance(c, dict))
            out.append(f"{p['acronym']} has {b.get('total_open', 0)} open NTSB recommendations: {counts}.")
            for r in (b.get("open") or [])[:5]:
                if isinstance(r, dict):
                    out.append(f"- [{r['srid']}] issued {fmt(r['issued'])}, rated {r['label']}, repeated {r['times_reiterated']}x.")
        cands = [c for call in f.get("calls") or [] if call.get("tool") == "find_candidates"
                 for c in (call.get("result") or {}).get("candidates") or [] if isinstance(c, dict)]
        if cands:
            out.append("Unverified proposals (not NTSB findings): " + "; ".join(
                f"[{c['id']}] ({', '.join(f'{k} {v}' for k, v in c['signals'].items())})" for c in cands[:5]) + ".")
        out.append("A linked warning does not establish that acting on it would have prevented the crash.")
        return AIMessage(content="\n".join(out))
