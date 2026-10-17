# Graph schema

Two layers in one FalkorDB instance: the shared `ntsb` graph (official facts, read-only for agents) and one
workspace graph per user/tenant (`ws_<name>`) for agent memory. Queries cannot cross graphs; workspace nodes
store `srid` / `ntsb_num` strings and the app resolves them against `ntsb` with a second query.

## Layer 1 — `ntsb` (shared)

### Nodes
| Label | Key properties | Source |
| --- | --- | --- |
| Recommendation | srid, text, status_code, status_label, is_open, closed_date, issued_date (int yyyymmdd), issued_from ('report'/'letter'/'id'), most_wanted, times_reiterated, letter_url, text_embedding | recommendations file |
| Accident | ntsb_num, event_date, city, state, country, lat, lon, fatal, injury_level, probable_cause, narrative, in_pool, partial, family_id, cause_embedding | both investigation files + Notations for legacy cases |
| Report | report_num (normalised), report_date, board_date, kind (AAR/AIR/SIR/study) | ReportNumber + numbers parsed from letters |
| Organization | name, acronym | Addressees, operators |
| Letter | date, type, from_ntsb, summary | Correspondence (deduplicated) |
| Status | code, label | fixed list |
| FindingCode | code, tier1..tier4 | Vehicles.Findings |
| EventType | cictt_group, phase | Vehicles.Events |

### Edges
| Edge | Meaning |
| --- | --- |
| (Recommendation)-[:ISSUED_FROM]->(Accident or Report) | origin |
| (Recommendation)-[:REITERATED_IN {date}]->(Report) | NTSB repeated it in a later report |
| (Recommendation)-[:RECLASSIFIED_IN {date, new_status}]->(Report) | status changed by a later report |
| (Recommendation)-[:MENTIONED_IN {date}]->(Report) | discussed only; NOT a link |
| (Recommendation)-[:SUPERSEDED_BY]->(Recommendation) | replaced (323 references) |
| (Report)-[:ABOUT]->(Accident) | joins reports to crashes |
| (Recommendation)-[:ADDRESSED_TO {status}]->(Organization) | owner and its own status |
| (Letter)-[:ABOUT]->(Recommendation); (Letter)-[:FROM|TO]->(Organization) | the ticket thread |
| (Recommendation)-[:HAD_STATUS {valid_from, valid_to, recorded_at, source_letter}]->(Status) | bi-temporal status history |
| (Accident)-[:HAS_FINDING {in_probable_cause}]->(FindingCode) | coded causes |
| (Accident)-[:HAD_EVENT]->(EventType) | CICTT event + phase |
| (Accident)-[:OPERATED_BY]->(Organization) | operator (enables "crashed while a warning addressed to it was open") |
Every edge carries `source: 'ntsb_official'`.

### Indexes
Exact: Recommendation.srid, Accident.ntsb_num, Report.report_num, Organization.name, Letter.date.
Full-text: Recommendation.text. Vector: Recommendation.text_embedding, Accident.cause_embedding.

## Layer 2 — `ws_<name>` (per workspace)
| Node | Fields | Memory type |
| --- | --- | --- |
| Investigation | question, started, finished, outcome | episodic |
| Step (chained by NEXT) | agent, tool, cypher, result_count, ms | episodic; feeds "how we got here" |
| Task | kind, status (queued/claimed/done), claimed_by, lease_until, payload | shared state / handoffs |
| Agent | name, role | coordination |
| Candidate | srid, ntsb_num, confidence, evidence, rules_applied, status (proposed/confirmed/rejected), valid_from, recorded_at | semantic |
| Rule | pattern, weight, hits, misses; LEARNED_FROM → Candidate | procedural |
| Brief | text, status (draft/blocked/approved), citations[]; APPROVED_BY → Approval | action + gate |
| Watch | target_kind, target_id | persistent context |

## Design rules
1. Store dates as integers `yyyymmdd` (FalkorDB date handling is awkward; integer comparison avoids timezone issues).
2. Normalise report numbers to `[A-Z0-9]` at load; keep the display form as a property.
3. Never create a REITERATED_IN/RECLASSIFIED_IN edge to a report about the recommendation's own origin accident.
4. Dedupe letters before creating Letter nodes.
5. Keep official and agent-derived facts in separate graphs; never write agent output into `ntsb`.
6. Heavy algorithms run at load/refresh and store results as node properties (family_id, impact_score, bridge_score).

## Signature Cypher (verify syntax against FalkorDB docs on day 1)
```cypher
// 1. Linked warnings open on the crash date (headline)
MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident {ntsb_num: $id})
WHERE NOT (r)-[:ISSUED_FROM]->(a)
  AND r.issued_date < a.event_date
  AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN r.srid, r.status_label, r.issued_date, r.times_reiterated ORDER BY r.issued_date

// 2. Proof of absence (negative answer)
MATCH (a:Accident {ntsb_num: $id})
OPTIONAL MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a)
RETURN count(r) = 0 AS no_linked_warning

// 3. All crashes that happened while a linked warning was open
MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(later:Accident)
WHERE NOT (r)-[:ISSUED_FROM]->(later) AND r.issued_date < later.event_date
  AND (r.closed_date IS NULL OR r.closed_date > later.event_date)
RETURN later.ntsb_num, later.city, later.event_date, later.fatal, collect(r.srid) AS open_warnings
ORDER BY later.fatal DESC

// 4. Status on a given day (bi-temporal)
MATCH (r:Recommendation {srid: $srid})-[h:HAD_STATUS]->(s:Status)
WHERE h.valid_from <= $date AND (h.valid_to IS NULL OR h.valid_to > $date)
RETURN s.label

// 5. Supersession chain
MATCH p = (r:Recommendation {srid: $srid})-[:SUPERSEDED_BY*1..10]->(last)
RETURN p ORDER BY length(p) DESC LIMIT 1

// 6. Owner backlog
MATCH (o:Organization {acronym: 'FAA'})<-[a:ADDRESSED_TO]-(r:Recommendation)
WHERE a.status = 'OUA' RETURN r.srid, r.issued_date, r.times_reiterated ORDER BY r.issued_date

// 7. Candidate generation (unstated links) — structural pass before vector re-ranking
MATCH (r:Recommendation {is_open: true})-[:ISSUED_FROM]->(o:Accident)-[:HAS_FINDING]->(f:FindingCode)<-[:HAS_FINDING]-(later:Accident)
WHERE later.event_date > r.issued_date AND later.in_pool
  AND NOT (r)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(later)
RETURN r.srid, later.ntsb_num, count(f) AS shared_findings ORDER BY shared_findings DESC LIMIT 25

// 8. Letter thread for a recommendation
MATCH (l:Letter)-[:ABOUT]->(r:Recommendation {srid: $srid})
OPTIONAL MATCH (l)-[:FROM]->(from:Organization)
RETURN l.date, l.type, l.from_ntsb, from.acronym, l.summary ORDER BY l.date
```
Expected size: ~6k recommendations, ~11k accidents, ~43k letters (after dedupe, far fewer), a few hundred
organisations and codes. All queries should return in milliseconds; show the timing on screen.
