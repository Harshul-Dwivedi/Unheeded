"""Tool checks against the loaded `ntsb` graph (run `make load` first). Skipped when FalkorDB is not reachable."""
import pytest

from unheeded import tools as T
from unheeded.loader import GRAPH, connect


@pytest.fixture(scope="module")
def g():
    try:
        db = connect()
        db.connection.ping()
    except Exception:
        pytest.skip("FalkorDB not reachable")
    if GRAPH not in db.list_graphs():
        pytest.skip("graph not loaded")
    return db.select_graph(GRAPH)


NOME = "ANC25MA018"
NOME_LINKED = ["A-13-012", "A-13-013", "A-16-034", "A-16-035", "A-22-021", "A-22-022", "A-24-017", "A-24-018"]


def test_resolve(g):
    assert T.resolve_accident(g, "Nome")["matches"][0]["ntsb_num"] == NOME
    assert T.resolve_accident(g, "anc25ma018")["matches"][0]["ntsb_num"] == NOME
    assert T.resolve_accident(g, "Kekaha Hawaii", year=2019)["matches"][0]["ntsb_num"] == "ANC20MA010"
    assert T.resolve_accident(g, "!!")["matches"] == []


def test_open_warnings_and_absence(g):
    w = {x["srid"]: x for x in T.find_open_warnings(g, NOME)["warnings"]}
    assert sorted(w) == NOME_LINKED
    assert w["A-16-035"]["status_on_crash_date"] == "Open - Unacceptable Response" and w["A-16-035"]["status_since"] == 20200608
    assert w["A-22-021"]["status_on_crash_date"].startswith("Open") and w["A-22-021"]["current_status"].startswith("Closed")
    assert T.prove_no_linked_warning(g, NOME)["no_linked_warning"] is False
    assert T.prove_no_linked_warning(g, "NOPE00XX000")["found"] is False
    ketchikan = T.prove_no_linked_warning(g, "ANC15MA041")  # report exists; its one repeat was issued after the crash
    assert ketchikan["no_linked_warning"] and ketchikan["reports"] == ["AAR-17-02"]
    assert [dict(x) for x in ketchikan["repeated_outside_definition"]] == [{"srid": "A-16-036", "why": "issued after the crash"}]
    assert T.prove_no_linked_warning(g, "CEN24FA240")["has_report"] is False  # McKinney: nothing to search
    assert T.resolve_accident(g, "GAA24WA016")["matches"] == []  # foreign investigation (Japan): out of scope
    assert all(m["ntsb_num"][5:7] not in ("WA", "RA") for m in T.resolve_accident(g, "Toronto")["matches"])


def test_status_chain_backlog_letters(g):
    assert T.status_on_date(g, "A-16-035", 20190101)["status"]["code"] == "OAAR"
    assert T.status_on_date(g, "A-16-035", 20100101)["status"] is None  # before it existed
    c = T.trace_chain(g, "A-00-066")
    assert [x["srid"] for x in c["superseded_by"]] == ["A-24-004", "A-24-005", "A-24-006"]
    assert [x["srid"] for x in c["supersedes"]] == ["A-91-029"]
    b = T.owner_backlog(g, "FAA", limit=10)
    assert b["total_open"] == sum(x["n"] for x in b["counts"]) and len(b["open"]) == 10 and b["truncated"]
    assert all(x["status"].startswith("O") for x in b["open"])
    letters = T.get_letters(g, "A-16-035")["letters"]
    assert letters == sorted(letters, key=lambda x: x["date"] or 0) and {"FAA", "NTSB"} <= {x["from_org"] for x in letters}


def test_candidates_never_repeat_official_links(g):
    c = T.find_candidates(g, ntsb_num=NOME)
    assert c["verified"] is False and c["excluded_official"] == NOME_LINKED
    assert c["candidates"] and not {x["id"] for x in c["candidates"]} & set(NOME_LINKED)
    assert [x["score"] for x in c["candidates"]] == sorted((x["score"] for x in c["candidates"]), reverse=True)
    r = T.find_candidates(g, srid="A-16-035")
    assert NOME not in {x["id"] for x in r["candidates"]} and NOME in r["excluded_official"]
    with pytest.raises(ValueError):
        T.find_candidates(g)


def test_score_weights():
    s = {"text": 0.5, "findings": 10, "events": 3, "family": 1}
    assert T.score(s, None) == pytest.approx(0.5 + 0.5 + 0.3 + 0.2)  # findings capped at 5
    assert T.score(s, {"text": 0, "findings": 0, "events": 0, "family": 1}) == 1


def test_similar_path_sources(g):
    s = T.similar_cases(g, NOME, limit=3)["similar"]
    assert len(s) == 3 and NOME not in {x["ntsb_num"] for x in s}
    p = T.explain_path(g, "A-16-035", NOME)["path"]
    assert [n["id"] for n in p["nodes"]] == ["A-16-035", "AIR-26-05", NOME] and p["edges"] == ["REITERATED_IN", "ABOUT"]
    src = T.get_sources(g, ["A-16-035", "AIR-26-05"])
    assert src["recommendations"][0]["letter_url"].endswith(".pdf") and src["reports"][0]["accidents"] == [NOME]
