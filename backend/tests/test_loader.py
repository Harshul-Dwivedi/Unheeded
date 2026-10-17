from unheeded.loader import build, classified_as, status_history, superseded_pairs


def letter(date, kind, text, from_ntsb=True):
    return {"CorrespondenceDate": date, "CorrespondenceType": kind, "IsFromNtsb": from_ntsb, "ResponseSummary": text}


def rec(srid, origin, letters, acc_date="2015-11-10T00:00:00", report="AAR-16-03", closed=None, subject="TO THE FAA: Do X."):
    return {"Srid": srid, "Subject": subject, "OverallStatus": "OUA" if not closed else "CAA",
            "OpenClosed": "Open - Unacceptable Response", "DateClosed": closed, "Priority": "CLASS II", "MostWanted": False,
            "TimesReiterated": 1, "GreenSheet": "", "Mode": "Aviation",
            "Notations": [{"NtsbNum": origin, "ReportNum": report, "AccidentDate": acc_date, "City": "", "State": ""}],
            "Addressees": [{"Name": "Federal Aviation Administration", "AddressAcronym": "FAA", "AddresseeStatus": "OUA",
                            "OpenClosedAddressee": "Open - Unacceptable Response", "Correspondence": letters}]}


def inv(n, date, report, **kw):
    return {"NTSBNumber": n, "EventDateTimeUTC": date, "ReportNumber": report, "ReportDate": kw.pop("rd", None),
            "Mode": "Aviation", "IsStudy": False, "Vehicles": [], **kw}


INV = [
    inv("CEN16MA036", "2015-11-10 19:00:00", "AAR1603", rd="2016-10-24"),
    inv("ANC25MA018", "2025-02-06 23:00:00", "AIR2605"),
    inv("CEN19MA141", "2019-05-13 20:21:00", "AAR2104"),
    {"NTSBNumber": "DCA22SS002", "IsStudy": True, "ReportNumber": "AIR2207", "ReportDate": "2022-08-01", "Mode": "Aviation"},
]
reit = "From Aviation Investigation Report AIR-26-05 ..."
RECS = [
    # linked: reiterated in Nome's report; letters duplicated as in the export
    rec("A-16-035", "CEN16MA036", [letter("2026-07-30", "Recommendation Reiteration", reit)] * 7),
    # mention only -> MENTIONED_IN, never a link
    rec("A-21-049", "CEN16MA036", [letter("2026-07-30", "Recommendation Mention", reit)]),
    # self-link: reiterated in its own origin report -> no edge
    rec("A-16-036", "CEN16MA036", [letter("2017-01-01", "Recommendation Reiteration", "In AAR-16-03 we ...")]),
    # study origin -> Report, not Accident; multi-aircraft suffix -> base accident
    rec("A-22-021", "DCA22SS002", [], acc_date="0001-01-01T00:00:00", report="AIR-22-07"),
    rec("A-21-010", "CEN19MA141AB", [letter("2021-05-01", "Transmittal Letter", "x")], acc_date="2019-05-13T00:00:00", report="AAR-21-04"),
    # non-aviation record riding along via TopicMode -> dropped
    {**rec("M-20-001", "CEN16MA036", []), "Mode": "Marine"},
]


def edges(model, t):
    return {(e["s"], e["d"]) for k, rows in model["edges"].items() if k[0] == t for e in rows}


def test_build():
    m = build(RECS, INV, [])
    recs = {r["srid"]: r for r in m["nodes"]["Recommendation"]}
    assert "M-20-001" not in recs
    assert recs["A-16-035"]["issued_date"] == 20161024 and recs["A-16-035"]["issued_from"] == "report"
    assert recs["A-22-021"]["issued_date"] == 20220801
    assert recs["A-21-010"]["issued_from"] == "letter"  # base CEN19MA141 has no ReportDate in this fixture
    assert edges(m, "REITERATED_IN") == {("A-16-035", "AIR2605")}
    assert edges(m, "MENTIONED_IN") == {("A-21-049", "AIR2605")}
    assert ("AIR2605", "ANC25MA018") in edges(m, "ABOUT")
    assert ("A-22-021", "AIR2207") in edges(m, "ISSUED_FROM") and ("A-21-010", "CEN19MA141") in edges(m, "ISSUED_FROM")
    assert {a["ntsb_num"] for a in m["nodes"]["Accident"]} == {"CEN16MA036", "ANC25MA018", "CEN19MA141"}
    assert len([l for l in m["nodes"]["Letter"] if l.get("type") == "Recommendation Reiteration" and "AIR-26-05" in l["summary"]]) == 1
    rel = next(e for k, rows in m["edges"].items() if k[0] == "REITERATED_IN" for e in rows)
    assert rel["p"] == {"date": 20260730, "letters": 1}


def test_superseded_pairs():
    assert superseded_pairs("A-00-066", "(Supersedes A-91-029; Superseded by A-24-4 through -6)") == [
        ("A-91-029", "A-00-066"), ("A-00-066", "A-24-004"), ("A-00-066", "A-24-005"), ("A-00-066", "A-24-006")]
    assert superseded_pairs("A-93-001", "(Superseded by A-93-8 through A-93-10)") == [
        ("A-93-001", "A-93-008"), ("A-93-001", "A-93-009"), ("A-93-001", "A-93-010")]


def test_classified_as():
    own = "Pending FAA action, Safety Recommendation A-16-35 is classified \u201cOpen\u2014Unacceptable Response.\u201d"
    other = "We classified Safety Recommendation A-09-90 \u201cClosed\u2014Unacceptable Action.\u201d " + own
    assert classified_as("A-16-035", own) == "OUA"
    assert classified_as("A-16-035", other) == "OUA"  # the A-09-90 sentence is not ours
    assert classified_as("A-09-090", other) == "CUA"
    assert classified_as("A-16-035", "A-16-34 through -36 remain classified OPEN--ACCEPTABLE RESPONSE.") == "OAA"
    assert classified_as("A-16-035", "Closed\u2014Unaccetpable Action/ No Response Received") == "CUAN"


def test_status_history():
    ev = [(20170406, "Official Correspondence", "OAA", "L1"), (20200608, "Recommendation Reiteration", "OAA", "L2"),
          (20200608, "Report Reclassification", "OUA", "L3"), (20230101, "Official Correspondence", "OUA", "L4")]
    h = status_history(20161024, None, "OUA", ev)
    assert [(x["code"], x["valid_from"], x.get("valid_to")) for x in h] == [
        ("OAR", 20161024, 20170406), ("OAA", 20170406, 20200608), ("OUA", 20200608, None)]
    assert h[-1]["letter"] == "L3"
    # closed rec with no closing letter: ends at DateClosed; letters after closure ignored
    h = status_history(20100101, 20150601, "CAA", [(20120101, "x", "OAA", "L1"), (20230310, "x", "CUA", "L2")])
    assert [(x["code"], x["valid_from"]) for x in h] == [("OAR", 20100101), ("OAA", 20120101), ("CAA", 20150601)]
    # open rec whose current status is newer than any letter: day unknown, flagged
    h = status_history(20100101, None, "OUA", [(20201231, "x", "OAA", "L1")])
    assert h[-1] == {"code": "OUA", "valid_from": 20210101, "basis": "current_record", "exact": False}
    # closed the day it was issued: one segment, no zero-length default
    assert status_history(20111216, 20111216, "CAA", []) == [{"code": "CAA", "valid_from": 20111216, "basis": "date_closed", "exact": True}]
