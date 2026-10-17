"""HTTP API end to end (stub model): entity pages, run -> SSE -> decision, review queue, isolation."""
import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from unheeded import memory as M
from unheeded.config import connect


@pytest.fixture(scope="module")
def client():
    try:
        if "ntsb" not in connect("agent_ro").list_graphs():
            pytest.skip("graph not loaded")
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")
    os.environ["LLM_PROVIDER"] = "stub"
    from unheeded.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ws():
    name = f"test_{uuid.uuid4().hex[:8]}"
    yield name
    M.drop_workspace(name)


def events(client, thread):
    out, kind = [], None
    with client.stream("GET", f"/api/runs/{thread}/events") as r:
        for line in r.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                out.append((kind, json.loads(line[6:])))
                if kind == "end":
                    break
    return out


def test_entity_pages(client):
    a = client.get("/api/accident/anc25ma018").json()
    assert len(a["linked"]) == 8 and a["accident"]["fatal"] == 10 and a["rated_unacceptable_today"] == 5
    w = next(x for x in a["linked"] if x["srid"] == "A-16-035")
    assert w["status_then"] == "Open - Unacceptable Response" and w["open_days"] == 3027 and w["origin_ntsb"] == "CEN16MA036"
    assert "A-21-049" in {m["srid"] for m in a["mentions"]} and a["absence"] is None
    r = client.get("/api/rec/A-16-035").json()
    assert [c["ntsb_num"] for c in r["linked_crashes"]] == ["CEN17MA183", "CEN19FA072", "ANC20MA010", "DCA20MA059", "ANC25MA018"]
    assert [h["code"] for h in r["history"]] == ["OAR", "OAA", "OAAR", "OUA"] and r["owners"][0]["acronym"] == "FAA"
    import re
    person_header = re.compile(r"^-?\s*From [^:]{1,160}, (?:Acting )?(?:Administrator|Chairman|Director|Secretary)")
    assert not any("Huerta" in (l["summary"] or "") or person_header.search(l["summary"] or "") for l in r["letters"])  # no individuals
    o = client.get("/api/org/FAA").json()
    assert o["total_open"] == 228 and len(o["unacceptable"]) == 71
    i = client.get("/api/insights").json()
    assert (i["linked_crashes"], i["links"], i["killed"]) == (16, 54, 48)
    assert {"Recommendation"} <= {h["kind"] for h in client.get("/api/search", params={"q": "A-16-03"}).json()["results"]}
    assert "ANC25MA018" in {h["id"] for h in client.get("/api/search", params={"q": "Nome"}).json()["results"]}
    assert client.get("/api/accident/NOPE00XX000").status_code == 404
    assert client.get("/api/rec/A-99-999").status_code == 404
    assert client.get("/api/workspaces/Bad Name!/review").status_code == 400


def test_run_stream_decide(client, ws):
    start = client.post("/api/runs", json={"question": "Was the Nome crash warned about?", "workspace": ws}).json()
    ev = events(client, start["thread"])
    kinds = [k for k, _ in ev]
    assert kinds.count("step") >= 9 and kinds[-2:] == ["result", "end"]
    result = ev[-2][1]
    assert result["paused"] and result["brief"]["status"] == "awaiting_approval" and len(result["facts"]["open_warnings"]) == 8
    assert any(e["cypher"] for k, e in ev if k == "step")  # exact Cypher streamed to the UI
    other = f"test_{uuid.uuid4().hex[:8]}"
    assert client.post(f"/api/workspaces/{other}/runs/{start['thread']}/decision", json={"action": "approve"}).status_code == 403
    from unheeded import api as API
    API.RUNS.clear()  # like an API restart: the decision must still work from the on-disk checkpoint
    done = client.post(f"/api/workspaces/{ws}/runs/{start['thread']}/decision", json={"action": "approve", "reviewer": "tester"}).json()
    assert done["status"] == "approved"
    inv = client.get(f"/api/workspaces/{ws}/investigations/{result['investigation']}").json()
    assert inv["briefs"][0]["status"] == "approved" and inv["briefs"][0]["decided_by"] == "tester" and inv["briefs"][0]["thread"]
    assert client.get(f"/api/workspaces/{ws}/investigations").json()["investigations"][0]["brief"]["status"] == "approved"

    rv = client.get(f"/api/workspaces/{ws}/review").json()
    assert rv["proposals"] and rv["weights"] == rv["default_weights"] and rv["precision"] is None and rv["fitted"] is False
    c = rv["proposals"][0]
    body = {"srid": c["srid"], "ntsb_num": c["ntsb_num"], "verdict": "confirmed", "reviewer": "tester"}
    after = client.post(f"/api/workspaces/{ws}/review", json=body).json()
    assert after["rules"]["confirmed"] == 1 and after["precision"] == 1.0 and after["decided"][0]["srid"] == c["srid"]
    assert client.post(f"/api/workspaces/{ws}/review", json=body).status_code == 409  # counted once
    assert client.post(f"/api/workspaces/{ws}/runs/{start['thread']}/decision", json={"action": "approve"}).status_code == 404  # already decided
    assert client.post(f"/api/workspaces/{ws}/review", json={**body, "srid": "DROP"}).status_code == 422


def test_alert_settings_and_refresh_guard(client, ws, monkeypatch):
    fresh = f"test_{uuid.uuid4().hex[:8]}"  # never written: every read must answer, not 500
    for path in ("review", "investigations", "watches", "alert-settings"):
        assert client.get(f"/api/workspaces/{fresh}/{path}").status_code == 200, path
    assert client.get(f"/api/workspaces/{ws}/alert-settings").json() == {"status": True, "repeat": True, "letters": True, "new": False}
    assert client.put(f"/api/workspaces/{ws}/alert-settings", json={"letters": False}).json()["letters"] is False
    monkeypatch.delenv("ALLOW_REFRESH", raising=False)
    assert client.get("/api/refresh").json()["enabled"] is False
    assert client.post("/api/refresh").status_code == 403  # rewrites the official graph: off unless the operator enables it
