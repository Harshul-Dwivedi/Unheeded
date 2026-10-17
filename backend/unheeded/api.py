"""HTTP API for the web app (docs/architecture.md, 11). The UI never talks to FalkorDB directly.

Run: uv run uvicorn unheeded.api:app --host 127.0.0.1 --port 8000
Local demo: binds to localhost and has no user login. Workspace names are validated, and a paused run can only be
decided through the workspace it belongs to.
"""
import asyncio
import datetime
import json
import os
import time
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from unheeded import agents as A
from unheeded import memory as M
from unheeded import queries as Q
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import DATA_DIR

app = FastAPI(title="Unheeded", version="0.1.0")
EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"


@lru_cache(maxsize=1)
def ntsb():
    return connect("agent_ro").select_graph("ntsb")


def rows(q, **p):
    res = ntsb().ro_query(q, p, timeout=T.TIMEOUT_MS)
    names = [h[1] for h in res.header]
    return [dict(zip(names, r)) for r in res.result_set]


def ws_reader(ws):
    try:
        return M.reader(ws)
    except ValueError:
        raise HTTPException(400, "workspace names are [a-z0-9_]{1,40}")


def days_between(a, b):
    d = lambda x: datetime.date(x // 10000, x // 100 % 100, x % 100)
    return (d(b) - d(a)).days if a and b else None


# --- runs: start, stream, decide -----------------------------------------------------------------------------------

class Run:
    def __init__(self, thread, workspace, question):
        self.thread, self.workspace, self.question = thread, workspace, question
        self.events, self.done, self.changed = [], False, asyncio.Event()

    def push(self, kind, data):
        self.events.append({"event": kind, "data": data})
        self.changed.set()


RUNS: dict[str, Run] = {}  # live event streams only (in-process); paused runs themselves are checkpointed on disk


class Ask(BaseModel):
    question: str = Field(min_length=3, max_length=400)
    workspace: str = "demo"


class Decision(BaseModel):
    action: str = Field(pattern="^(approve|edit|send_back|reject)$")
    reviewer: str = Field(default="reviewer", max_length=80)
    text: str | None = Field(default=None, max_length=20000)
    note: str | None = Field(default=None, max_length=1000)


def summary(state):
    """What the UI needs from a final/paused LangGraph state (no Cypher bulk in facts; steps carry it)."""
    f = state.get("facts", {})
    b = dict(state.get("brief") or {})
    return {"status": state.get("status"), "paused": state.get("paused"), "investigation": state.get("investigation"),
            "thread": state.get("thread_id"), "brief": b, "decision": state.get("decision"),
            "facts": {k: f.get(k) for k in ("plan", "accident", "other_matches", "did_you_mean", "open_warnings", "absence",
                                            "similar", "chain", "backlog", "sources", "analyst_note")},
            "calls": [{"tool": c["tool"], "args": c["args"], "result": c["result"]} for c in f.get("calls", [])],
            "steps": state.get("steps"), "tasks": state.get("board")}


@app.post("/api/runs")
async def start_run(ask: Ask):
    ws_reader(ask.workspace)
    thread = f"{ask.workspace}:{time.time_ns()}"
    run = RUNS[thread] = Run(thread, ask.workspace, ask.question)

    async def work():
        A.log.info("run %s start  ws=%s  %s", thread, ask.workspace, ask.question[:120])
        try:
            state = await A.run(ask.question, ask.workspace, on_step=lambda e: run.push("step", e), thread_id=thread)
            run.push("result", summary(state))
            A.log.info("run %s %s  brief %s", thread, state.get("status"), (state.get("brief") or {}).get("status"))
        except Exception as e:  # surfaced to the client, never swallowed
            A.log.info("run %s error  %s", thread, e)
            run.push("error", {"message": str(e)[:500]})
        finally:
            run.done = True
            run.changed.set()

    asyncio.create_task(work())
    return {"thread": thread, "events": f"/api/runs/{thread}/events"}


@app.get("/api/runs/{thread}/events")
async def run_events(thread: str):
    run = RUNS.get(thread)
    if not run:
        raise HTTPException(404, "unknown run (runs live in this server process only)")

    async def stream():
        i = 0
        while True:
            while i < len(run.events):
                e = run.events[i]
                i += 1
                yield f"event: {e['event']}\ndata: {json.dumps(e['data'], default=str)}\n\n"
            if run.done:
                yield "event: end\ndata: {}\n\n"
                return
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/api/workspaces/{ws}/runs/{thread}/decision")
async def decide(ws: str, thread: str, d: Decision):
    ws_reader(ws)
    if not thread.startswith(f"{ws}:"):  # ownership: a run is decided only through its own workspace
        raise HTTPException(403, "this run belongs to another workspace")
    if d.action == "edit" and not d.text:
        raise HTTPException(400, "edit needs text")
    if not await A.pending(thread):  # checkpoints are on disk: this works after an API restart too
        raise HTTPException(404, "no paused run with that id")
    A.log.info("run %s resume  %s by %s", thread, d.action, d.reviewer)
    state = await A.resume(thread, d.model_dump(exclude_none=True))
    A.log.info("run %s %s  brief %s", thread, state.get("status"), (state.get("brief") or {}).get("status"))
    return summary(state)


# --- entity pages --------------------------------------------------------------------------------------------------

@app.get("/api/accident/{ntsb_num}")
def accident(ntsb_num: str):
    head = rows(Q.ACCIDENT_DETAIL, id=ntsb_num.upper())
    if not head:
        raise HTTPException(404, "no such accident")
    a = head[0]["accident"]
    linked = rows(Q.ACCIDENT_LINKED, id=a["ntsb_num"])
    for w in linked:
        w["open_days"] = days_between(w["issued"], a["event_date"])
    return {"accident": a, "reports": head[0]["reports"], "linked": linked, "mentions": rows(Q.ACCIDENT_MENTIONS, id=a["ntsb_num"]),
            "rated_unacceptable_today": sum(1 for w in linked if w["code_today"] in ("OUA", "CUA", "CUAS", "CUAN")),
            "rated_unacceptable_then": sum(1 for w in linked if w["code_then"] == "OUA"),  # on the crash date
            "absence": None if linked else T.prove_no_linked_warning(ntsb(), a["ntsb_num"]),
            "similar": T.similar_cases(ntsb(), a["ntsb_num"], 5)["similar"]}


@app.get("/api/rec/{srid}")
def recommendation(srid: str):
    head = rows(Q.REC_DETAIL, srid=srid.upper())
    if not head or not head[0]["rec"]:
        raise HTTPException(404, "no such recommendation")
    h = head[0]
    chain = T.trace_chain(ntsb(), h["rec"]["srid"])
    return {"rec": h["rec"], "origin": h["origin"], "origin_kind": h["origin_kind"], "owners": h["owners"],
            "history": rows(Q.STATUS_HISTORY, srid=h["rec"]["srid"]), "linked_crashes": rows(Q.REC_LINKED_CRASHES, srid=h["rec"]["srid"]),
            "repeats": chain["repeats"], "superseded_by": chain["superseded_by"], "supersedes": chain["supersedes"],
            "letters": T.get_letters(ntsb(), h["rec"]["srid"], limit=200)["letters"]}  # names scrubbed by the tool


@app.get("/api/org/{acronym}")
def organisation(acronym: str):
    opened = rows(Q.OWNER_OPEN, acronym=acronym)
    if not opened:
        raise HTTPException(404, "no open recommendations for that organisation")
    periods = {}
    for r in opened:
        if r["status"] == "OUA":
            y = r["issued"] // 10000
            key = f"{y - y % 5}–{str(y - y % 5 + 4)[-2:]}"
            periods[key] = periods.get(key, 0) + 1
    total = rows(Q.OWNER_TOTAL, acronym=acronym)
    return {"owner": acronym, "name": opened[0]["owner_name"], "addressed": total[0]["n"] if total else None,
            "counts": rows(Q.OWNER_COUNTS, acronym=acronym), "total_open": len(opened),
            "unacceptable": [r for r in opened if r["status"] == "OUA"], "unacceptable_by_issue_period": dict(sorted(periods.items()))}


@app.get("/api/insights")
def insights():
    crashes = rows(Q.ALL_LINKED_CRASHES)
    where = {r["ntsb_num"]: r for r in rows("MATCH (a:Accident) WHERE a.ntsb_num IN $ids RETURN a.ntsb_num AS ntsb_num, a.lat AS lat, "
                                            "a.lon AS lon, a.state AS state", ids=[c["ntsb_num"] for c in crashes])}
    for c in crashes:
        c.update({k: where.get(c["ntsb_num"], {}).get(k) for k in ("lat", "lon", "state")})
    by_rec = {}
    for c in crashes:
        for srid in c["open_warnings"]:
            by_rec[srid] = by_rec.get(srid, 0) + 1
    top = sorted(by_rec, key=lambda k: (-by_rec[k], k))[:3]  # map filters: the warnings linked to the most crashes
    return {"crashes": sorted(crashes, key=lambda c: c["event_date"]), "linked_crashes": len(crashes),
            "links": sum(len(c["open_warnings"]) for c in crashes), "killed": sum(c["fatal"] or 0 for c in crashes),
            "recommendations": rows("MATCH (r:Recommendation) RETURN count(r) AS n")[0]["n"],
            "leaders": rows(Q.INSIGHTS_LEADERS), "most_repeated_unacceptable": rows(Q.MOST_REPEATED_OUA),
            "filters": [{"srid": s, "crashes": by_rec[s]} for s in top], "origins": rows(Q.REC_ORIGINS, srids=top),
            "faa": rows(Q.OWNER_COUNTS, acronym="FAA")}


@app.get("/api/search")
def search(q: str = Query(min_length=2, max_length=40)):
    term = q.strip().upper()
    hits = rows(Q.SEARCH, id=term)
    if len(term) >= 3 and term.isalpha():
        hits += [{"kind": "Accident", "id": m["ntsb_num"], "label": f"{m['city']}, {m.get('state') or ''}", "note": str(m["event_date"]),
                  "target": m["ntsb_num"]} for m in T.resolve_accident(ntsb(), q, limit=6)["matches"]]
    return {"q": q, "results": hits[:24]}


# --- workspaces: memory, review queue -------------------------------------------------------------------------------

@app.get("/api/workspaces")
def workspaces():
    return {"workspaces": M.workspaces() or ["demo"]}


@app.get("/api/workspaces/{ws}/investigations")
def list_investigations(ws: str):
    return {"workspace": ws, "investigations": M.investigations(ws_reader(ws))}


@app.get("/api/workspaces/{ws}/investigations/{inv_id}")
def get_investigation(ws: str, inv_id: str):
    out = M.investigation(ws_reader(ws), inv_id)
    if not out:
        raise HTTPException(404, "no such investigation in this workspace")
    return out


class Review(BaseModel):
    srid: str = Field(pattern=r"^A-\d{2}-\d{3}$")
    ntsb_num: str = Field(pattern=r"^[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]*$")
    verdict: str = Field(pattern="^(confirmed|rejected)$")
    reviewer: str = Field(default="reviewer", max_length=80)


def review_payload(ws):
    g = ws_reader(ws)
    items = M.proposals(g, status=None, limit=300)
    if items:
        recs = {r["srid"]: r for r in rows("MATCH (r:Recommendation) WHERE r.srid IN $s RETURN r.srid AS srid, substring(r.text, 0, 200) AS text, "
                                           "r.status_label AS status", s=sorted({i["srid"] for i in items}))}
        accs = {a["ntsb_num"]: a for a in rows("MATCH (a:Accident) WHERE a.ntsb_num IN $n RETURN a.ntsb_num AS ntsb_num, a.city AS city, "
                                               "a.state AS state, a.event_date AS event_date, a.fatal AS fatal", n=sorted({i["ntsb_num"] for i in items}))}
        for i in items:
            i["evidence"] = json.loads(i["evidence"]) if i.get("evidence") else {}
            i["rec"], i["accident"] = recs.get(i["srid"]), accs.get(i["ntsb_num"])
    r = M.rules(g)
    return {"workspace": ws, "proposals": [i for i in items if i["status"] == "proposed"],
            "decided": [i for i in items if i["status"] != "proposed"], "rules": r, "weights": M.weights(g),
            "fitted": r["decided"] >= M.MIN_DECISIONS and 0 < r["confirmed"] < r["decided"],
            "default_weights": T.DEFAULT_WEIGHTS,
            "precision": round(r["confirmed"] / r["decided"], 3) if r["decided"] else None}


@app.get("/api/workspaces/{ws}/review")
def review(ws: str):
    return review_payload(ws)


class Undo(BaseModel):
    srid: str = Field(pattern=r"^A-\d{2}-\d{3}$")
    ntsb_num: str = Field(pattern=r"^[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]*$")
    reviewer: str = Field(default="reviewer", max_length=80)


@app.post("/api/workspaces/{ws}/review/undo")
def undo_review(ws: str, u: Undo):
    ws_reader(ws)
    if not M.undo_feedback(M.workspace(ws), u.srid, u.ntsb_num, u.reviewer):
        raise HTTPException(409, "unknown or undecided candidate")
    return review_payload(ws)


@app.post("/api/workspaces/{ws}/review")
def post_review(ws: str, rv: Review):
    ws_reader(ws)
    if not M.record_feedback(M.workspace(ws), rv.srid, rv.ntsb_num, rv.verdict, rv.reviewer):
        raise HTTPException(409, "unknown or already decided candidate")
    return review_payload(ws)


class Watch(BaseModel):
    kind: str = Field(pattern="^(Recommendation|Accident|Organization)$")
    target_id: str = Field(min_length=2, max_length=60)


@app.post("/api/workspaces/{ws}/watches")
def add_watch(ws: str, w: Watch):
    ws_reader(ws)
    M.add_watch(M.workspace(ws), w.kind, w.target_id)
    return {"ok": True}


@app.get("/api/workspaces/{ws}/watches")
def list_watches(ws: str):
    g = ws_reader(ws)
    rows_ = M._ro(g, "MATCH (w:Watch) OPTIONAL MATCH (a:Alert)-[:FOR]->(w) RETURN w.kind, w.target_id, w.created, collect(a {.srid, .change, .at}) "
                     "ORDER BY w.created DESC")  # _ro: a never-written workspace reads as empty
    watches = [{"kind": k, "target_id": t, "created": c, "alerts": [x for x in a if x.get("srid")]} for k, t, c, a in rows_]
    ids = lambda kind: [w["target_id"] for w in watches if w["kind"] == kind]
    recs = {r["srid"]: r for r in rows("MATCH (r:Recommendation) WHERE r.srid IN $s RETURN r.srid AS srid, r.status_code AS code, "
                                       "r.status_label AS label", s=ids("Recommendation"))}
    accs = {r["ntsb_num"]: r for r in rows("MATCH (a:Accident) WHERE a.ntsb_num IN $n RETURN a.ntsb_num AS ntsb_num, a.city AS city, "
                                           "a.state AS state, a.event_date AS event_date", n=ids("Accident"))}
    orgs = {o: sum(c["n"] for c in rows(Q.OWNER_COUNTS, acronym=o)) for o in ids("Organization")}
    for w in watches:
        w["now"] = recs.get(w["target_id"]) if w["kind"] == "Recommendation" else accs.get(w["target_id"]) if w["kind"] == "Accident" \
            else {"open": orgs.get(w["target_id"], 0)}
    return {"watches": watches}


class AlertSettings(BaseModel):
    status: bool | None = None
    repeat: bool | None = None
    letters: bool | None = None
    new: bool | None = None


@app.get("/api/workspaces/{ws}/alert-settings")
def get_alert_settings(ws: str):
    return M.alert_settings(ws_reader(ws))


@app.put("/api/workspaces/{ws}/alert-settings")
def put_alert_settings(ws: str, a: AlertSettings):
    ws_reader(ws)
    return M.set_alert_settings(M.workspace(ws), a.model_dump(exclude_none=True))


# --- refresh: admin step, off unless the server is started with ALLOW_REFRESH=1 --------------------------------------
REFRESH = {"running": False, "last": None, "error": None, "started": None}


def refresh_enabled():
    return os.environ.get("ALLOW_REFRESH") == "1" and bool(os.environ.get("REFRESH_DATA_DIR"))


@app.get("/api/refresh")
def refresh_status():
    return {"enabled": refresh_enabled(), **REFRESH}


@app.post("/api/refresh")
async def start_refresh():
    if not refresh_enabled():  # rewrites the official graph: fail closed unless the operator turned it on
        raise HTTPException(403, "refresh is off; start the API with ALLOW_REFRESH=1 and REFRESH_DATA_DIR=<dir with the newer export>")
    if REFRESH["running"]:
        raise HTTPException(409, "a refresh is already running")
    REFRESH.update(running=True, error=None, started=int(time.time() * 1000))

    def work():
        from unheeded import enrich, refresh as R
        try:
            out = R.refresh(os.environ["REFRESH_DATA_DIR"])  # path from server config, never from the request
            db = connect()
            g = db.select_graph("ntsb")
            enrich.embed(g), enrich.families(g), enrich.impact(db, g)
            REFRESH["last"] = {**out, "finished": int(time.time() * 1000)}
        except Exception as e:  # reported to the page, never swallowed
            REFRESH["error"] = str(e)[:500]
        finally:
            REFRESH["running"] = False

    asyncio.get_running_loop().run_in_executor(None, work)
    return {"started": True}


# --- method / eval card -------------------------------------------------------------------------------------------

@app.get("/api/eval")
def eval_card():
    def load(name):
        p = EVAL_DIR / f"report_{name}.json"
        return json.loads(p.read_text()) if p.exists() else None
    e1, e2, e3, e4, cc = (load(n) for n in ("eval1", "eval2", "eval3", "eval4", "concurrency"))
    gold = json.loads((DATA_DIR.parent / "derived" / "gold_links_strict_definition.json").read_text())
    return {
        "eval1": e1 and {"pass": e1["pass"], "recall": e1["recall"], "false_positives": len(e1["g_none"]["false_positives"]),
                         "median_ms": e1["find_open_warnings_ms"]["median_server"]},
        "eval2": e2 and {"pass": e2["pass"], "average": e2["average"]},
        "eval3": e3 and {"pass": e3["pass"], "errors": e3["claims"]["errors"], "claims": e3["claims"]["total"]},
        "eval4": e4 and {"pass": e4["pass"], "p95_ms": e4["latency"]["p95_ms"]},
        "concurrency": cc and {"claims_ok": cc["claims"]["ok"], "writes_per_s": cc["many_graphs"]["writes_per_s"]},
        "gold": {"crashes": gold["crashes"], "links": gold["links"]},
    }


@app.get("/api/health")
def health():
    newest = max(f.stat().st_mtime for f in DATA_DIR.glob("*.json"))
    c = rows("MATCH (r:Recommendation) RETURN count(r) AS n, sum(CASE WHEN r.is_open THEN 1 ELSE 0 END) AS open, "
             "sum(CASE WHEN r.status_code = 'OUA' THEN 1 ELSE 0 END) AS oua")[0]
    return {"ok": True, "graph": "ntsb", "recommendations": c["n"], "open": int(c["open"]), "open_unacceptable": int(c["oua"]),
            "data_date": int(datetime.datetime.fromtimestamp(newest).strftime("%Y%m%d"))}  # when the CAROL files were downloaded
