# Graph algorithms

| # | Algorithm | Answers | Used by | When it runs |
| --- | --- | --- | --- | --- |
| 1 | Multi-hop pattern matching, variable-length paths (`SUPERSEDED_BY*1..10`) | linked warnings; supersession chains; owner backlogs | Analyst via MCP tools | live |
| 2 | Shortest path (`algo.SPpaths` / `shortestPath`) | "how is this crash connected to that warning"; negative proof "no path within k hops" | explain_path, prove_no_linked_warning, UI path view | live |
| 3 | Community detection (`algo.labelPropagation`) over Accident–FindingCode/EventType projection | failure families (e.g. Part 135 loss of control; tour-helicopter weather) | Scout/Analyst candidate search within a family; memory consolidation | load/refresh → `family_id` |
| 4 | PageRank (`algo.pageRank`) over Recommendation→later-Accident link graph | most consequential ignored warnings leaderboard | Insights | load/refresh → `impact_score` |
| 5 | Betweenness (`algo.betweenness`) on FindingCode–Accident projection | systemic issues that bridge many families | Insights (optional) | load/refresh → `bridge_score` |
| 6 | WCC (`algo.WCC`) on SUPERSEDED_BY + shared reports | warning families; orphan check | optional | load |
| 7 | BFS (`algo.BFS`) | alert reach in watch mode | optional | refresh |
| — | Vector KNN on cause/text embeddings | similar_cases; candidate re-ranking | find_candidates | live |

Must-have for the demo: 1, 2, 3. Showcase: 4 (and 5 if time). Precompute 3–6 and store as properties so every
live agent step stays in milliseconds. Verify on day 1: procedure names/signatures, whether PageRank and
betweenness accept weights (weight by fatalities in post-processing if not), vector index syntax.
