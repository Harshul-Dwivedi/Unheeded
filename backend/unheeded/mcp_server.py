"""Unheeded MCP server: the graph tools (unheeded/tools.py) over MCP, logged in as the read-only agent_ro user.

Run (stdio, what Claude Code and LangGraph spawn):  uv run python -m unheeded.mcp_server
Run (HTTP, for a long-lived server):               uv run python -m unheeded.mcp_server http   -> 127.0.0.1:8765/mcp
"""
import sys
from functools import lru_cache

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from unheeded import memory as M
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import GRAPH

mcp = FastMCP(
    "unheeded",
    instructions=(
        "Read-only tools over the NTSB aviation graph. State only dates, statuses and ids these tools return. "
        "A 'linked warning' means the NTSB repeated or reclassified the recommendation in that crash's own report, "
        "it was issued before the crash, and it was open on the crash date; it never implies the crash would have "
        "been prevented. find_candidates results are unverified proposals, never facts. Use NTSB status wording "
        "(e.g. 'rated unacceptable by the NTSB'). Never name individuals. Dates are yyyymmdd integers."
    ),
    host="127.0.0.1",
    port=8765,
    log_level="WARNING",  # stdio clients show stderr; per-request INFO lines are noise
)
RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


@lru_cache(maxsize=1)
def graph():
    return connect("agent_ro").select_graph(GRAPH)


@mcp.tool(annotations=RO)
def resolve_accident(text: str, year: int | None = None, limit: int = 5) -> dict:
    """Find a crash by NTSB number (e.g. ANC25MA018) or place name (e.g. 'Nome', 'Kekaha Hawaii'), optional year.
    Returns matches with date, place, deaths and report numbers; several matches mean ask or pick by date/state."""
    return T.resolve_accident(graph(), text, year, limit)


@mcp.tool(annotations=RO)
def find_open_warnings(ntsb_num: str) -> dict:
    """Linked warnings for a crash: recommendations the NTSB repeated or reclassified in this crash's report, issued
    before it and open on the crash date. Each row gives status_on_crash_date and, separately, current_status."""
    return T.find_open_warnings(graph(), ntsb_num)


@mcp.tool(annotations=RO)
def prove_no_linked_warning(ntsb_num: str) -> dict:
    """Negative answer with its evidence: whether the crash exists (found), whether it has no linked warning, and what
    was searched (edge types, path, hop budget, conditions). found=false means unknown crash, not 'no warning'."""
    return T.prove_no_linked_warning(graph(), ntsb_num)


@mcp.tool(annotations=RO)
def status_on_date(srid: str, date: int) -> dict:
    """NTSB status of a recommendation (e.g. A-16-035) on a day (yyyymmdd). exact=false means the change day is
    unknown; status=null means the recommendation did not exist yet."""
    return T.status_on_date(graph(), srid, date)


@mcp.tool(annotations=RO)
def trace_chain(srid: str) -> dict:
    """Supersession both ways (recommendations that replaced it / that it replaced, with hops) and every report that
    repeated, reclassified or merely mentioned it."""
    return T.trace_chain(graph(), srid)


@mcp.tool(annotations=RO)
def owner_backlog(acronym: str, limit: int = 50) -> dict:
    """Open recommendations addressed to an organisation (acronym like FAA, or full name): exact counts by status,
    plus the oldest first (capped; truncated=true when more exist)."""
    return T.owner_backlog(graph(), acronym, limit)


@mcp.tool(annotations=RO)
def get_letters(srid: str, limit: int = 50) -> dict:
    """The letter thread for a recommendation, oldest first: date, type, from/to organisation, text."""
    return T.get_letters(graph(), srid, limit)


@mcp.tool(annotations=RO)
def find_candidates(ntsb_num: str | None = None, srid: str | None = None, limit: int = 10, workspace: str | None = None) -> dict:
    """UNVERIFIED proposals for links the NTSB never made. Give a crash (ntsb_num) to rank recommendations open on its
    date, or a recommendation (srid) to rank later pool crashes. Each candidate lists its evidence signals
    (text similarity, shared finding codes, shared events, same family, seed). Official links are excluded.
    workspace: use that workspace's learned rule weights and confirmed links as seeds."""
    weights, seeds = None, ()
    if workspace:
        mem = M.reader(workspace)  # read-only (agent_ro)
        weights, seeds = M.weights(mem), M.seeds(mem)
    return T.find_candidates(graph(), ntsb_num=ntsb_num, srid=srid, weights=weights, seeds=seeds, limit=limit)


@mcp.tool(annotations=RO)
def similar_cases(ntsb_num: str, limit: int = 10) -> dict:
    """Crashes whose probable cause reads most like this one's (similarity 0..1), with a same-failure-family flag."""
    return T.similar_cases(graph(), ntsb_num, limit)


@mcp.tool(annotations=RO)
def explain_path(srid: str, ntsb_num: str, max_len: int = 4) -> dict:
    """Shortest connection between a recommendation and a crash (nodes and edge types), for showing why they relate."""
    return T.explain_path(graph(), srid, ntsb_num, max_len)


@mcp.tool(annotations=RO)
def get_sources(ids: list[str]) -> dict:
    """Citations: recommendation ids -> official letter PDF, report numbers -> crash ids, letter ids -> excerpt."""
    return T.get_sources(graph(), ids)


if __name__ == "__main__":
    mcp.run("streamable-http" if sys.argv[1:] == ["http"] else "stdio")
