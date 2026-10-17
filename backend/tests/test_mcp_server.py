"""The MCP server over real stdio, as Claude Code / LangGraph spawn it. Needs the loaded graph and .env."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from unheeded.config import connect

EXPECTED = {"resolve_accident", "find_open_warnings", "prove_no_linked_warning", "status_on_date", "trace_chain",
            "owner_backlog", "get_letters", "find_candidates", "similar_cases", "explain_path", "get_sources"}


def call(steps):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=["-m", "unheeded.mcp_server"],
                                   cwd=str(Path(__file__).parents[1]), env={**os.environ})

    async def run():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            return await steps(s)
    return asyncio.run(run())


@pytest.fixture(scope="module", autouse=True)
def needs_graph():
    try:
        if "ntsb" not in connect("agent_ro").list_graphs():
            pytest.skip("graph not loaded")
    except Exception as e:
        pytest.skip(f"FalkorDB / agent_ro not available: {e}")


def test_tools_listed_read_only():
    async def steps(s):
        return (await s.list_tools()).tools
    tools = call(steps)
    assert {t.name for t in tools} == EXPECTED
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in tools)


def test_calls_return_graph_facts():
    async def steps(s):
        out = {}
        for name, args in (("find_open_warnings", {"ntsb_num": "ANC25MA018"}),
                           ("status_on_date", {"srid": "A-16-035", "date": 20250206}),
                           ("prove_no_linked_warning", {"ntsb_num": "NOPE00XX000"})):
            res = await s.call_tool(name, args)
            assert not res.isError, res.content
            out[name] = json.loads(res.content[0].text)
        bad = await s.call_tool("find_candidates", {})  # neither id: tool error, not a crash
        out["bad"] = bad.isError
        return out
    out = call(steps)
    assert len(out["find_open_warnings"]["warnings"]) == 8
    assert out["status_on_date"]["status"]["code"] == "OUA"
    assert out["prove_no_linked_warning"]["found"] is False
    assert out["bad"] is True
