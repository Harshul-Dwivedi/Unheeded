# Evaluation spec

Four evals prove the four claims the demo makes. All run from one command (`make eval`) in CI with no human in
the loop, so a judge can reproduce the numbers.

## Gold data
A gold link = (recommendation, later crash) pair meeting the strict definition (`03-data.md`). Current export:
**54 gold links across 16 crashes** (`data/derived/gold_links_strict_definition.json`, reproducible with
`data/scripts/gold_links.py`).
| Set | Contents | Used by |
| --- | --- | --- |
| G-gold | 54 pairs, 16 crashes | Eval 1, Eval 2 |
| G-mention | pairs where the report only mentions the recommendation (e.g. A-21-049 → AIR-26-05) | Eval 3 negatives |
| G-none | 50 fatal accidents from the 2021–2026 aviation pool with no cross-reference to any recommendation | Eval 1 negatives, Eval 3 |
| Pool | 2021–2026 export, Mode = Aviation | Eval 2 candidate search |

## Eval 1 — Linked-warning recovery
`find_open_warnings(ntsb_num)` must return exactly the gold links for the 16 crashes and nothing for the 50 G-none accidents.
| Metric | Target |
| --- | --- |
| Recall on gold links | 100% (54/54) |
| Precision on gold crashes | 100% |
| False positives on G-none | 0/50 |
| Median query time | < 20 ms |
Traps to catch: self-links to the origin accident; mentions counted as links; issuance after the crash
(Togiak vs A-16-035); ±1 day local/UTC drift; report-number formats.

## Eval 2 — The learning curve (hidden-half protocol) — proves Track 2 without a human
1. Split G-gold by crash into halves A and B (8 crashes each, stratified so both contain recorder and non-recorder links).
2. Delete REITERATED_IN and RECLASSIFIED_IN edges for both halves from the eval graph.
3. Round 1 (cold): run `find_candidates` for every crash in A and B; rank top 10 with default rule weights
   (signals: CICTT event+phase, finding codes where present, text similarity recommendation text ↔ probable cause, family).
4. Feedback: treat A's gold links as confirmations and every other top-10 candidate for A as rejections;
   write Candidate nodes and run the same rule-weight update the Review queue uses.
5. Round 2 (warm): re-run on B with updated weights and A's confirmed candidates as retrieval seeds.
| Metric on B | Cold | Warm | Pass if |
| --- | --- | --- | --- |
| Hit@10 (gold link in top 10) | measured | measured | warm ≥ cold + 10 points |
| MRR of first gold hit | measured | measured | warm > cold |
| Candidates checked per crash | measured | measured | warm ≤ cold |
Swap halves and average. If warm does not beat cold, soften the Track 2 claim in the pitch.
Scope note: structured codes are dense only from ~2008; state this on the Method page.

## Eval 3 — The approval gate (plain code)
40 fixed claims: 20 should pass, 20 should be blocked.
| Claim type | n | Example | Must |
| --- | --- | --- | --- |
| Gold link stated correctly | 10 | A-16-035 linked to Nome, open 6 Feb 2025 | pass |
| Status correct | 5 | A-16-035 unacceptable since 8 Jun 2020 | pass |
| Count correct | 5 | eight linked warnings open at Nome | pass |
| Mention dressed as link | 6 | A-21-049 linked to Nome | block |
| Wrong date/status | 6 | A-16-035 closed in 2023 | block |
| Issued after the crash | 4 | A-16-035 linked to Togiak | block |
| Names an individual | 4 | any sentence naming a pilot/official | block |
Errors in either direction: 0. Each verdict names the path searched and whether a weaker path (mention) was found.
Negative proof: for all 50 G-none, `prove_no_linked_warning` returns true, names the edge types and hop budget, < 20 ms.

## Eval 4 — Graph necessity and latency
| Query | Graph-native because | Target |
| --- | --- | --- |
| Supersession chain `SUPERSEDED_BY*1..10` | unknown depth | full chain < 10 ms |
| Shortest path crash → oldest linked warning | SPpaths; drawn as explanation | < 10 ms |
| Failure families | labelPropagation | families cover ≥ 80% of coded accidents |
| Most consequential warnings | PageRank over warning→crash links | top 5 includes A-13-013 and A-16-035 |
| Per-workspace memory | separate graphs | two concurrent investigations do not touch each other's nodes |
| Every live tool call | p50/p95 over the eval run | p95 < 50 ms |
Latency is read from Step nodes; nothing in the UI may be hard-coded. No SQL bake-off; show Cypher and timings.

## Reporting
`make eval` → `eval/report.json` + Markdown summary in README. The demo eval card shows: gold recall and
false positives (E1); Hit@10 cold vs warm averaged over both splits (E2); gate errors = 0 (E3); p95 latency (E4);
plus export date, row counts after dedupe, git commit.
Schedule: Day 1 E1 green before agent code · Day 2 E3+E4 wired · Day 3 E2 run twice · Day 4 `make eval` in CI.
