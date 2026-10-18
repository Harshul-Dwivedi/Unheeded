# System architecture

## Principle
The graph holds the facts, the LLM reasons, plain code holds authority.
- Facts come only from graph queries; the LLM never states a date, status or ID a query did not return.
- The LLM decides what to look at and writes the brief.
- Plain code verifies citations, enforces the approval gate, updates rule weights, loads data.
- No web search anywhere in the agent loop. External calls: Claude API (reasoning) and an embedding model
  (at load time; a local sentence-transformers model is acceptable).

## Layers
```
Next.js web app (Investigate, entity pages, Review queue, Agent board, Briefs, Watch, Insights, Method, ⌘K)
   │ REST for pages · SSE for live agent steps
FastAPI (Python)
   ├─ /investigate   start a LangGraph run; stream Step events
   ├─ /approve       resume the paused run (human gate)
   ├─ /review        confirm/reject a Candidate → Rule update
   ├─ /entity/*      accident, recommendation, organisation, letters
   ├─ /refresh       re-load CAROL exports, diff, write HAD_STATUS history, raise Watch alerts
   └─ MCP clients → Unheeded MCP server + FalkorDB MCP server (read-only)
FalkorDB  (graph ntsb + ws_* graphs; vector + full-text indexes)
Loader   (CAROL JSON → dedupe → nodes/edges → indexes → embeddings → algorithms → properties)
```
The UI never talks to FalkorDB directly.

## MCP tools (Unheeded server; each wraps a tested Cypher template, read-only, timeout, LIMIT cap)
| Tool | Returns | Backed by |
| --- | --- | --- |
| resolve_accident(text) | ntsb_num + facts | full-text / id match |
| find_open_warnings(ntsb_num) | linked warnings open on the crash date | query 1 |
| prove_no_linked_warning(ntsb_num) | boolean + edge types searched + hop budget + ms | query 2 |
| status_on_date(srid, date) | status label | query 4 |
| trace_chain(srid) | supersession chain + reiterations | query 5 |
| owner_backlog(acronym) | open recommendations by status, oldest first | query 6 |
| find_candidates(ntsb_num | srid) | ranked unstated-link candidates with evidence | query 7 + vector rerank + Rule weights |
| similar_cases(ntsb_num) | nearest accidents by cause text + same family | vector index + family_id |
| explain_path(srid, ntsb_num) | shortest path nodes/edges for the UI | algo.SPpaths |
| get_sources(ids) | letter URLs, report numbers | Letter/Report nodes |
Memory tools (workspace graph): log_step, create_task, claim_task, complete_task, save_candidate, record_feedback, get_rules, get_history.
FalkorDB MCP server (`query_graph`, read-only mode) is exposed for ad-hoc questions, guarded the same way.

## Agents (LangGraph; Claude as the model)
| Agent | Job | Tools |
| --- | --- | --- |
| Orchestrator | understands the question, loads workspace memory and rules, plans, assembles the answer | get_history, get_rules |
| Scout | resolves the accident/recommendation, gathers facts and comparable cases; writes a Task | resolve_accident, similar_cases, create_task |
| Analyst | claims the Task; official links via find_open_warnings; unstated links via find_candidates; scores with evidence and rules; writes Candidates | find_open_warnings, status_on_date, find_candidates, save_candidate |
| Clerk | drafts the brief; every sentence cites a recommendation, report or letter | get_sources |
| Approval gate (plain code, not an LLM) | checks every citation against the graph; blocks mention-dressed-as-link, wrong dates/statuses, post-crash issuance, any sentence naming an individual; pauses the run (LangGraph interrupt) until a person approves | direct Cypher |
Agents coordinate through Task nodes with leases in the workspace graph, not by chat. The board UI reads those nodes.

## One run, step by step ("Was the Nome crash warned about?")
1. Orchestrator creates an Investigation node; loads past Alaska investigations and Rules.
2. Scout resolves to ANC25MA018; writes Task check_warnings.
3. Analyst claims it; find_open_warnings → 8 linked; status_on_date ×8; find_candidates → 1 proposal (Candidate, unverified).
4. Clerk drafts the brief (≈14 citations).
5. Gate verifies each citation; blocks any claim that fails (example: "A-21-049 linked" → only a mention); pauses for approval.
6. Human approves (or sends back / removes the claim). Brief saved to the workspace.
Every tool call is a Step node with its Cypher and wall-clock ms, shown live in the UI.

## Memory
| | Short-term | Long-term |
| --- | --- | --- |
| Where | LangGraph state | workspace graph |
| Lifespan | one run | persistent per workspace |
| Holds | plan, intermediate results | Investigations, Steps, Tasks, Candidates, Rules, Briefs, Watches |
Learning loop: reviewer confirms/rejects Candidates → plain code updates Rule weights (hit/miss counts) →
next find_candidates loads the rules first → fewer candidates checked, higher precision. Proven by Eval 2.

## Modes
- Ask mode: the flow above.
- Watch mode: /refresh loads a new export, diffs statuses/letters/reiterations, writes HAD_STATUS history,
  alerts workspaces watching the affected recommendation/organisation/accident.

## Guardrails (also in the method page)
Read-only agents; timeouts; LIMIT caps; nothing published without approval;
unverified proposals appear only under a labelled "Unverified" heading and never count toward the answer; no individuals named; official vs proposed facts stored in separate graphs.
