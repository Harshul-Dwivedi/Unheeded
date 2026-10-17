"""Eval 3: the approval gate (docs/evals.md). 40 fixed claims, 20 must pass and 20 must be blocked, with 0 errors.

Claims are written out by hand here (the answer key does not come from the code under test). A "block" counts only
if the expected check fired. Also:
  - negative proof: prove_no_linked_warning is true for all 50 G-none accidents, names what it searched, < 20 ms
  - no false blocks on the agents' own stub briefs for all 16 gold crashes (skip with --no-agents)
Writes eval/report_eval3.json; exits 1 on any error.
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from eval.eval1 import g_none
from unheeded import gate as G
from unheeded import tools as T
from unheeded.config import connect
from unheeded.loader import DATA_DIR, read

OUT = Path(__file__).parent / "report_eval3.json"
LINK = "was a linked warning for [{c}]: the NTSB repeated it in that crash's report while it was still open."

# (type, subject, text, expected: "pass" or the check that must block it)
CLAIMS = [
    # 10 gold links stated correctly -> pass
    *[("gold_link", c, f"[{s}] " + LINK.format(c=c), "pass") for s, c in [
        ("A-16-035", "ANC25MA018"), ("A-13-013", "ANC25MA018"), ("A-24-018", "ANC25MA018"), ("A-16-035", "CEN17MA183"),
        ("A-16-035", "CEN19FA072"), ("A-16-035", "ANC20MA010"), ("A-16-035", "DCA20MA059"), ("A-16-036", "CEN19MA141"),
        ("A-03-016", "DCA04MA011"), ("A-09-027", "DCA15FA085")]],
    # 5 statuses stated correctly -> pass
    ("status", None, "[A-16-035] has been rated Open - Unacceptable Response by the NTSB since 8 Jun 2020.", "pass"),
    ("status", None, "[A-13-013] is currently rated Open - Unacceptable Response by the NTSB.", "pass"),
    ("status", None, "[A-22-021] was closed in 2026 and is currently rated Closed - Acceptable Action by the NTSB.", "pass"),
    ("status", None, "[A-24-017] is currently rated Open - Acceptable Response by the NTSB.", "pass"),
    ("status", None, "On 6 Feb 2025 [A-22-021] was open.", "pass"),
    # 5 counts stated correctly -> pass
    ("count", "ANC25MA018", "Eight linked warnings were open when [ANC25MA018] happened.", "pass"),
    ("count", "CEN17MA183", "Six linked warnings were open when [CEN17MA183] happened.", "pass"),
    ("count", "ANC20MA010", "Eight linked warnings were open when [ANC20MA010] happened.", "pass"),
    ("count", "DCA20MA059", "Four linked warnings were open when [DCA20MA059] happened.", "pass"),
    ("count", "CEN19FA072", "Four linked warnings were open when [CEN19FA072] happened.", "pass"),
    # 6 mentions dressed as links -> link
    *[("mention_as_link", "ANC25MA018", f"[{s}] " + LINK.format(c="ANC25MA018"), "link")
      for s in ("A-21-049", "A-03-065", "A-10-026", "A-15-029", "A-09-090", "A-99-061")],
    # 6 wrong dates / statuses -> status or issued
    ("wrong_status", None, "[A-16-035] was closed in 2023.", "status"),
    ("wrong_status", None, "On 6 Feb 2025 [A-16-035] was rated Open - Acceptable Response by the NTSB.", "status"),
    ("wrong_status", None, "[A-13-013] is currently rated Closed - Acceptable Action by the NTSB.", "status"),
    ("wrong_status", None, "[A-24-018] has been rated Open - Unacceptable Response by the NTSB since 8 Jun 2020.", "status"),
    ("wrong_status", None, "[A-22-021] is currently open.", "status"),
    ("wrong_date", None, "[A-16-034] was issued 23 Jul 2013.", "issued"),
    # 4 issued after the crash -> link
    ("issued_after", "ANC17MA001", "[A-16-035] " + LINK.format(c="ANC17MA001"), "link"),
    ("issued_after", "DCA20MA059", "[A-20-029] " + LINK.format(c="DCA20MA059"), "link"),
    ("issued_after", "DCA24MA063", "[A-24-009] " + LINK.format(c="DCA24MA063"), "link"),
    ("issued_after", "ANC20MA010", "[A-21-005] " + LINK.format(c="ANC20MA010"), "link"),
    # 4 names an individual (fictional names) -> person
    ("person", None, "Captain Daniel Ortiz had reported the same problem before the crash.", "person"),
    ("person", None, "FAA Administrator Robert K. Mills never acted on [A-16-035].", "person"),
    ("person", None, "The pilot, Sarah Collins, flew into icing conditions.", "person"),
    ("person", None, "Inspector James Whitfield approved the flight.", "person"),
]
assert len(CLAIMS) == 40 and sum(c[3] == "pass" for c in CLAIMS) == 20


async def agent_briefs():
    from unheeded import agents as A
    from unheeded import memory as M
    from unheeded.stub_llm import StubChat
    gold = json.load(open(DATA_DIR.parent / "derived" / "gold_links_strict_definition.json"))
    out = []
    try:
        for item in gold["items"]:
            s = await A.run(f"Was the crash {item['ntsb_num']} warned about?", "eval3", model=StubChat())
            v = s["brief"]["verdict"]
            out.append({"crash": item["ntsb_num"], "passed": v["passed"], "claims": v["claims_checked"], "blocked": v["blocked"]})
    finally:
        M.drop_workspace("eval3")
    return out


def main():
    g = connect("agent_ro").select_graph("ntsb")
    results, errors, ms = [], [], []
    for kind, subject, text, expected in CLAIMS:
        t = time.perf_counter()
        v = G.check(text, subject, g)
        ms.append((time.perf_counter() - t) * 1000)
        checks = {b["check"] for b in v["blocked"]}
        ok = v["passed"] if expected == "pass" else (expected in checks)
        row = {"type": kind, "subject": subject, "text": text, "expected": expected, "verdict": "pass" if v["passed"] else "block",
               "checks": sorted(checks), "reasons": [b["reason"] for b in v["blocked"]],
               "paths_searched": sorted({b["path_searched"] for b in v["blocked"] if b["path_searched"]}),
               "weaker_paths": sorted({b["weaker_path"] for b in v["blocked"] if b["weaker_path"]}), "ok": ok}
        results.append(row)
        if not ok:
            errors.append(row)

    recs, _, pool = read(DATA_DIR)
    proofs, slow = [], []
    for n in g_none(recs, pool):
        p = T.prove_no_linked_warning(g, n)
        good = bool(p["found"] and p["no_linked_warning"] and p["edge_types_searched"] and p["hop_budget"])
        proofs.append(good)
        if p["ms"] >= 20:
            slow.append((n, p["ms"]))
    briefs = asyncio.run(agent_briefs()) if "--no-agents" not in sys.argv else []

    report = {
        "claims": {"total": len(results), "should_pass": 20, "should_block": 20,
                   "errors": len(errors), "false_blocks": sum(1 for e in errors if e["expected"] == "pass"),
                   "missed_blocks": sum(1 for e in errors if e["expected"] != "pass"),
                   "gate_ms_median": round(statistics.median(ms), 2), "gate_ms_max": round(max(ms), 2)},
        "negative_proof": {"g_none": len(proofs), "proven": sum(proofs), "over_20ms": slow},
        "agent_briefs": {"n": len(briefs), "passed": sum(b["passed"] for b in briefs),
                         "claims_checked": sum(b["claims"] for b in briefs),
                         "false_blocks": [b for b in briefs if not b["passed"]]},
        "errors": errors, "results": results,
    }
    report["pass"] = (not errors and all(proofs) and len(proofs) == 50 and not slow
                      and all(b["passed"] for b in briefs))
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("claims", "negative_proof", "pass")} |
                     {"agent_briefs": {k: report["agent_briefs"][k] for k in ("n", "passed", "claims_checked")}}, indent=1))
    for e in errors:
        print("ERROR", e["type"], "|", e["text"], "| expected", e["expected"], "| got", e["verdict"], e["checks"], e["reasons"][:2])
    sys.exit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
