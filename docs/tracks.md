# How one graph serves all three tracks

## Track 1 — Agents that act on connected data (native)
| Track asks for | Unheeded |
| --- | --- |
| Multi-hop reasoning | crash → report → recommendation → origin accident → owner; 4–5 hops |
| Graph queries/algorithms as tools | MCP tools wrapping Cypher + SPpaths, PageRank, CDLP |
| Tool selection via MCP | agents choose among tools; the choice is logged and shown |
| Actions based on the graph | drafts an accountability brief; human approval before anything is published |
| Explainable results | path drawn, Cypher shown, per-step latency, links to official letters |

## Track 2 — Agent memory and coordination (must be real, proven by Eval 2)
| Track asks for | Unheeded |
| --- | --- |
| Episodic | Investigation + Step nodes per run |
| Semantic | Candidate links with confidence/evidence, separate from official facts |
| Procedural | Rule nodes with weights learned from confirm/reject |
| Shared state / handoffs | Task nodes with leases claimed by Scout → Analyst → Clerk |
| Concurrency | two investigations in parallel, idempotent MERGE writes; shown on the board |
| Per-user / tenant | one workspace graph per user via multigraph; switching workspaces changes everything |
| Persistent history | "what did we find about helicopter tour crashes?" answered from the workspace |
Demo proof: same question type twice; second run checks fewer candidates with higher precision (Eval 2 chart).

## Track 3 — Company brain (framing; do these four and no more)
| Track asks for | Unheeded |
| --- | --- |
| Docs/tickets/chats in one graph | ~43k dated letters between the NTSB and recipients = a 60-year ticket thread |
| Who owns/knows what | Organization nodes; owner backlog; "what is the FAA sitting on" |
| Decisions traced to source | every status change links to the letter where the Board decided it |
| Keeping knowledge current | refresh + diff with bi-temporal HAD_STATUS edges; watch alerts |
| Answers with sources | every answer cites IDs, report numbers and letter URLs |
Pitch line: "Organisations forget. Unheeded is the institutional memory aviation safety never had."
Caveat: some judges picture Slack/Notion; the 1:1 mapping and the Method page are the defence.

## Remove-FalkorDB test
Without the graph: no multi-hop paths, no supersession chains, no proof of absence, no shared task state, no
per-workspace memory, no families/PageRank. Latency shown live per step.
