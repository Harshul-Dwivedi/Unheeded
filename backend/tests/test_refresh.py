"""Watch-mode refresh on an isolated graph copy with a deliberately modified export (never the live `ntsb` or real workspaces)."""
import copy
import json
import uuid

import pytest

from unheeded import memory as M
from unheeded import refresh as R
from unheeded.config import connect
from unheeded.loader import DATA_DIR, FILES, build, read, write


def test_diff_unit():
    old = ({"A-1": {"code": "OUA", "closed": None, "letters": 3}, "A-2": {"code": "OAA", "closed": None, "letters": 1}},
           {"A-1": {("REITERATED_IN", "AIR-26-05")}})
    new = ({"A-1": {"code": "CUA", "closed": 20261001, "letters": 4}, "A-2": {"code": "OAA", "closed": None, "letters": 1},
            "A-3": {"code": "OAR", "closed": None}}, {"A-1": {("REITERATED_IN", "AIR-26-05"), ("RECLASSIFIED_IN", "AIR-27-01")}})
    assert R.diff(old, new) == [("A-1", [("status", "status OUA → CUA"), ("status", "closed on 20261001"), ("letters", "1 new letter"),
                                         ("repeat", "reclassified in AIR-27-01")]),
                                ("A-3", [("new", "new recommendation, status OAR")])]


@pytest.fixture
def isolated(tmp_path):
    try:
        db = connect()
        if "ntsb" not in db.list_graphs():
            pytest.skip("graph not loaded")
    except Exception as e:
        pytest.skip(f"FalkorDB not available: {e}")
    name, ws = f"ntsb_refresh_{uuid.uuid4().hex[:6]}", f"test_{uuid.uuid4().hex[:8]}"
    recs, inv, pool = read(DATA_DIR)
    write(db.select_graph(name), build(recs, inv, pool))  # the "current" record
    newer = copy.deepcopy(recs)  # the "next export": A-16-035 closed unacceptable, with one new FAA letter
    r = next(x for x in newer if x["Srid"] == "A-16-035")
    r.update(OverallStatus="CUA", OpenClosed="Closed - Unacceptable Action", DateClosed="2026-10-01T00:00:00")
    faa = next(a for a in r["Addressees"] if a.get("AddressAcronym") == "FAA")
    faa["Correspondence"].append({"CorrespondenceDate": "2026-10-01T00:00:00", "CorrespondenceType": "Official Correspondence",
                                  "IsFromNtsb": True, "ResponseSummary": "Test fixture letter: classified Closed—Unacceptable Action."})
    (tmp_path / FILES["recs"]).write_text(json.dumps(newer))
    for k in ("inv", "pool"):
        (tmp_path / FILES[k]).symlink_to(DATA_DIR / FILES[k])
    g = M.workspace(ws)
    M.add_watch(g, "Recommendation", "A-16-035")
    M.add_watch(g, "Organization", "FAA")
    M.add_watch(g, "Accident", "ANC25MA018")
    M.add_watch(g, "Recommendation", "A-99-001")  # not reached
    yield name, ws, tmp_path
    db.select_graph(name).delete()
    M.drop_workspace(ws)


def test_refresh_diffs_reloads_and_alerts(isolated):
    name, ws, newer = isolated
    quiet = f"test_{uuid.uuid4().hex[:8]}"  # a second workspace that only wants repeats: a status change must not alert it
    M.add_watch(M.workspace(quiet), "Recommendation", "A-16-035")
    M.set_alert_settings(M.workspace(quiet), {"status": False, "letters": False})
    out = R.refresh(newer, graph=name, workspaces=[ws, quiet])
    M.drop_workspace(quiet)
    assert quiet not in out["alerts"]
    assert out["changes"] == [{"srid": "A-16-035", "change": ["status OUA → CUA", "closed on 20261001", "1 new letter"]}]
    hits = {(a["kind"], a["target_id"]) for a in out["alerts"][ws]}
    assert hits == {("Recommendation", "A-16-035"), ("Organization", "FAA"), ("Accident", "ANC25MA018")}
    g = connect("agent_ro").select_graph(name)
    assert g.ro_query("MATCH (r:Recommendation {srid: 'A-16-035'}) RETURN r.status_code").result_set == [["CUA"]]
    assert g.ro_query("MATCH (:Recommendation {srid: 'A-16-035'})-[h:HAD_STATUS]->(s:Status) WHERE h.valid_to IS NULL RETURN s.code").result_set == [["CUA"]]
    assert R.refresh(newer, graph=name, workspaces=[ws])["changes"] == []  # idempotent: same export twice, nothing new
    alerted = {t for (t,) in M.reader(ws).ro_query("MATCH (:Alert)-[:FOR]->(w:Watch) RETURN DISTINCT w.target_id").result_set}
    assert alerted == {"A-16-035", "FAA", "ANC25MA018"}  # A-99-001 is not reached
