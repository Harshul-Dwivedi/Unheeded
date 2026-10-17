"""Cypher templates (docs/graph-schema.md), checked against FalkorDB v4.20.7.

Fixes over the doc versions:
  - DISTINCT: a rec can be both reiterated and reclassified in one report, or a crash can have two reports
  - prove-absence applies the same three conditions as the headline query (the doc version only checked edges)
"""

# US aviation only: CAROL also holds foreign investigations the NTSB assisted (WA/RA in the id, a non-US country).
# Country is blank on some US offshore and territory records (Gulf blocks, Puerto Rico), so blank counts as US unless WA/RA.
US = "(a.country = 'USA' OR (a.country IS NULL AND NOT substring(a.ntsb_num, 5, 2) IN ['WA', 'RA']))"

# The ONE definition: repeated/reclassified in the crash's report, issued before, open on the crash date.
LINKED = """
MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident {ntsb_num: $id})
WHERE NOT (r)-[:ISSUED_FROM]->(a)
  AND r.issued_date < a.event_date
  AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
"""

# 1. Linked warnings open on the crash date (headline). Status on the crash date comes from HAD_STATUS; the current
#    status is labelled as such so nobody reads today's status as the status at the time.
FIND_OPEN_WARNINGS = LINKED + """
WITH DISTINCT r, a
OPTIONAL MATCH (r)-[h:HAD_STATUS]->(s:Status)
WHERE h.valid_from <= a.event_date AND (h.valid_to IS NULL OR h.valid_to > a.event_date)
RETURN r.srid AS srid, r.issued_date AS issued, s.label AS status_on_crash_date, h.valid_from AS status_since,
       h.exact AS status_date_exact, r.status_label AS current_status, r.times_reiterated AS times_reiterated
ORDER BY issued, srid
"""

# 2. Proof of absence (negative answer). Also says what was there to search: a crash with no NTSB report in the data
#    has no path by construction, so "no linked warning" there says nothing; repeats in its report that fall outside
#    the definition (issued after, closed before, its own) are returned so the answer can name them.
#    size(pattern) > 0, not a bare pattern: inside CASE a pattern is a list of paths and [] counts as true (FALKORDB-ISSUES #20)
PROVE_NO_LINKED_WARNING = """
MATCH (a:Accident {ntsb_num: $id})
OPTIONAL MATCH (p:Report)-[:ABOUT]->(a)
WITH a, collect(DISTINCT p.display) AS reports
OPTIONAL MATCH (w:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a)
WITH a, reports, collect(DISTINCT CASE WHEN w IS NULL THEN null ELSE {srid: w.srid,
     why: CASE WHEN size((w)-[:ISSUED_FROM]->(a)) > 0 THEN 'issued from this crash' WHEN w.issued_date >= a.event_date THEN 'issued after the crash'
               ELSE 'closed before the crash' END} END) AS repeated
OPTIONAL MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a)
WHERE NOT (r)-[:ISSUED_FROM]->(a)
  AND r.issued_date < a.event_date
  AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN a.ntsb_num AS ntsb_num, count(DISTINCT r) = 0 AS no_linked_warning, reports, repeated
"""
# grouping by `a` matters: a bare count() over zero rows still returns a row, so an unknown id would "prove" absence

# 3. All crashes that happened while a linked warning was open
ALL_LINKED_CRASHES = """
MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident)
WHERE NOT (r)-[:ISSUED_FROM]->(a)
  AND r.issued_date < a.event_date
  AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
WITH a, collect(DISTINCT r.srid) AS open_warnings
RETURN a.ntsb_num AS ntsb_num, a.city AS city, a.event_date AS event_date, a.fatal AS fatal, open_warnings
ORDER BY fatal DESC, ntsb_num
"""

# 4. Status on a given day (bi-temporal). `exact` false = the change day is unknown (after the last letter we have)
STATUS_ON_DATE = """
MATCH (r:Recommendation {srid: $srid})-[h:HAD_STATUS]->(s:Status)
WHERE h.valid_from <= $date AND (h.valid_to IS NULL OR h.valid_to > $date)
RETURN s.code AS code, s.label AS label, h.valid_from AS since, h.basis AS basis, h.exact AS exact, h.letter AS letter
"""

# 5. Supersession chain, both directions, every rec reached (chains fan out: A-00-066 -> A-24-004..006).
#    docs/graph-schema.md version fails on FalkorDB: ORDER BY length(p) after projecting p. Aggregate or project hops first.
SUPERSEDED_BY_CHAIN = """
MATCH p = (:Recommendation {srid: $srid})-[:SUPERSEDED_BY*1..10]->(n:Recommendation)
RETURN n.srid AS srid, min(length(p)) AS hops ORDER BY hops, srid LIMIT 200
"""
SUPERSEDES_CHAIN = """
MATCH p = (n:Recommendation)-[:SUPERSEDED_BY*1..10]->(:Recommendation {srid: $srid})
RETURN n.srid AS srid, min(length(p)) AS hops ORDER BY hops, srid LIMIT 200
"""
REPEATS = """
MATCH (:Recommendation {srid: $srid})-[e:REITERATED_IN|RECLASSIFIED_IN|MENTIONED_IN]->(p:Report)
OPTIONAL MATCH (p)-[:ABOUT]->(a:Accident)
RETURN type(e) AS how, e.date AS date, p.display AS report, collect(a.ntsb_num) AS accidents ORDER BY date
"""

# 6. Owner backlog. ADDRESSED_TO.status is the addressee's own status code (OUA, OAA, ...)
OWNER_BACKLOG = """
MATCH (o:Organization)<-[a:ADDRESSED_TO]-(r:Recommendation)
WHERE (o.acronym = $acronym OR o.name = $acronym) AND a.status STARTS WITH 'O'
RETURN a.status AS status, a.status_label AS label, r.srid AS srid, r.issued_date AS issued,
       r.times_reiterated AS times_reiterated
ORDER BY status, issued LIMIT $limit
"""

OWNER_COUNTS = """
MATCH (o:Organization)<-[a:ADDRESSED_TO]-(:Recommendation)
WHERE (o.acronym = $acronym OR o.name = $acronym) AND a.status STARTS WITH 'O'
RETURN a.status AS status, a.status_label AS label, count(*) AS n ORDER BY n DESC
"""

# 8. Letter thread
LETTERS = """
MATCH (l:Letter)-[:ABOUT]->(:Recommendation {srid: $srid})
OPTIONAL MATCH (l)-[:FROM]->(f:Organization)
OPTIONAL MATCH (l)-[:TO]->(t:Organization)
RETURN l.id AS id, l.date AS date, l.type AS type, f.acronym AS from_org, t.acronym AS to_org, l.summary AS summary
ORDER BY date LIMIT $limit
"""

# resolve_accident: exact id, else full-text on place
ACCIDENT_BY_ID = """
MATCH (a:Accident {ntsb_num: $id}) WHERE """ + US + """
OPTIONAL MATCH (p:Report)-[:ABOUT]->(a)
RETURN a.ntsb_num AS ntsb_num, a.event_date AS event_date, a.city AS city, a.state AS state, a.fatal AS fatal,
       a.in_pool AS in_pool, a.partial AS partial, collect(p.display) AS reports
"""
ACCIDENT_BY_PLACE = """
CALL db.idx.fulltext.queryNodes('Accident', $q) YIELD node AS a, score
WHERE ($year IS NULL OR a.event_date / 10000 = $year) AND """ + US + """
OPTIONAL MATCH (p:Report)-[:ABOUT]->(a)
RETURN a.ntsb_num AS ntsb_num, a.event_date AS event_date, a.city AS city, a.state AS state, a.fatal AS fatal,
       a.in_pool AS in_pool, a.partial AS partial, collect(p.display) AS reports, score
ORDER BY score DESC, size(reports) DESC, fatal DESC, event_date DESC LIMIT $limit
"""

# Unstated-link candidates (query 7, split into signals; plain code combines them with rule weights).
# Generators: text, shared findings, shared events. Family is a flag on what they found (the largest family holds 17%).
# for a crash: recs open on its date, minus its official links and its own recs
OFFICIAL_SRIDS = """
MATCH (r:Recommendation)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(:Accident {ntsb_num: $id})
RETURN collect(DISTINCT r.srid)
"""
# Date window first, then exact cosine on what is left (~400 recs open on a given day): same rows as asking the vector
# index for k=6000 and post-filtering (FALKORDB-ISSUES #7), checked on all 16 gold crashes, and ~40% faster
CRASH_TEXT = """
MATCH (a:Accident {ntsb_num: $id}) WHERE a.cause_embedding IS NOT NULL
MATCH (r:Recommendation) WHERE r.issued_date < a.event_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
  AND r.text_embedding IS NOT NULL AND NOT (r)-[:ISSUED_FROM]->(a)
RETURN r.srid, 1 - vec.cosineDistance(a.cause_embedding, r.text_embedding)
"""
CRASH_SHARED = """
MATCH (a:Accident {ntsb_num: $id}) WITH a
MATCH (a)-[:%s]->(x)<-[:%s]-(o:Accident) WHERE o <> a WITH a, x, o
MATCH (o)<-[:ISSUED_FROM]-(r:Recommendation)
WHERE r.issued_date < a.event_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN r.srid, count(DISTINCT x) AS n ORDER BY n DESC LIMIT $k
"""
CRASH_FAMILY = """
MATCH (a:Accident {ntsb_num: $id}), (r:Recommendation)-[:ISSUED_FROM]->(o:Accident)
WHERE r.srid IN $ids AND o.family_id = a.family_id
RETURN r.srid, 1
"""
# for a recommendation: later pool crashes while it was open, minus its official links and its origin
REC_OFFICIAL = """
MATCH (:Recommendation {srid: $srid})-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a:Accident)
RETURN collect(DISTINCT a.ntsb_num)
"""
REC_TEXT = """
MATCH (r:Recommendation {srid: $srid}) WHERE r.text_embedding IS NOT NULL
CALL db.idx.vector.queryNodes('Accident', 'cause_embedding', $k, r.text_embedding) YIELD node AS a, score
WHERE a.in_pool AND """ + US + """ AND a.event_date > r.issued_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
  AND NOT (r)-[:ISSUED_FROM]->(a)
RETURN a.ntsb_num, 1 - score
"""
REC_SHARED = """
MATCH (r:Recommendation {srid: $srid})-[:ISSUED_FROM]->(o:Accident) WITH r, o
MATCH (o)-[:%s]->(x)<-[:%s]-(a:Accident)
WHERE a.in_pool AND """ + US + """ AND a <> o AND a.event_date > r.issued_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN a.ntsb_num, count(DISTINCT x) AS n ORDER BY n DESC LIMIT $k
"""
REC_FAMILY = """
MATCH (:Recommendation {srid: $srid})-[:ISSUED_FROM]->(o:Accident), (a:Accident)
WHERE a.ntsb_num IN $ids AND a.family_id = o.family_id
RETURN a.ntsb_num, 1
"""

# similar_cases: nearest accidents by cause text (distance: 0 = identical)
SIMILAR_CASES = """
MATCH (a:Accident {ntsb_num: $id}) WHERE a.cause_embedding IS NOT NULL
CALL db.idx.vector.queryNodes('Accident', 'cause_embedding', $k, a.cause_embedding) YIELD node AS b, score
WHERE b <> a
RETURN b.ntsb_num AS ntsb_num, b.city AS city, b.event_date AS event_date, b.fatal AS fatal,
       1 - score AS similarity, b.family_id = a.family_id AS same_family
ORDER BY similarity DESC
"""

# explain_path: shortest path for the UI. SPpaths, undirected, over official edges plus probable-cause findings and
# defining events (hub findings pruned, loader.HUB_SHARE), so a path never runs through a code on 19% of all crashes
EXPLAIN_PATH = """
MATCH (r:Recommendation {srid: $srid}), (a:Accident {ntsb_num: $id})
CALL algo.SPpaths({sourceNode: r, targetNode: a, relDirection: 'both', maxLen: $max_len, pathCount: 1,
                   relTypes: ['REITERATED_IN', 'RECLASSIFIED_IN', 'ABOUT', 'ISSUED_FROM', 'HAS_PC_FINDING', 'HAD_DEFINING_EVENT']})
YIELD path
RETURN [n IN nodes(path) | {label: labels(n)[0], id: coalesce(n.srid, n.display, n.ntsb_num, n.code, n.id)}] AS nodes,
       [e IN relationships(path) | type(e)] AS edges
"""

# Memory-seeded retrieval: confirmed (srid, crash) pairs from a workspace boost / add candidates near them in meaning
# crash target: recs confirmed for crashes whose cause reads like this one
CRASH_SEEDS = """
MATCH (a:Accident {ntsb_num: $id}) WHERE a.cause_embedding IS NOT NULL
UNWIND $seeds AS seed
MATCH (b:Accident {ntsb_num: seed.c}) WHERE b <> a AND b.cause_embedding IS NOT NULL
MATCH (r:Recommendation {srid: seed.s})
WHERE r.issued_date < a.event_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date) AND NOT (r)-[:ISSUED_FROM]->(a)
RETURN r.srid, max(1 - vec.cosineDistance(a.cause_embedding, b.cause_embedding))
"""
# rec target: pool crashes that read like crashes already confirmed for this rec
REC_SEEDS = """
MATCH (r:Recommendation {srid: $srid})
UNWIND $seeds AS seed
WITH r, seed WHERE seed.s = r.srid
MATCH (b:Accident {ntsb_num: seed.c}) WHERE b.cause_embedding IS NOT NULL
CALL db.idx.vector.queryNodes('Accident', 'cause_embedding', $k, b.cause_embedding) YIELD node AS a, score
WHERE a.in_pool AND """ + US + """ AND a <> b AND a.event_date > r.issued_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
  AND NOT (r)-[:ISSUED_FROM]->(a)
RETURN a.ntsb_num, max(1 - score)
"""

# Watch mode: what a change to one recommendation reaches. algo.BFS(source, maxLevel, relType) is positional with one
# rel type or NULL (all); it follows outgoing edges: rec -> report -> accident, rec -> organisation, rec -> newer rec.
# Crashes it reaches are kept only if they are the rec's origin or linked to it by the one definition: BFS also walks
# MENTIONED_IN and repeats outside the open window (A-16-035 -> Togiak), which are not links.
ALERT_REACH = """
MATCH (r:Recommendation {srid: $srid})
CALL algo.BFS(r, $max_level, NULL) YIELD nodes
UNWIND [r] + nodes AS n
WITH r, n WHERE n:Recommendation OR n:Organization
   OR (n:Accident AND ((r)-[:ISSUED_FROM]->(n)
       OR ((r)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(n)
           AND r.issued_date < n.event_date AND (r.closed_date IS NULL OR r.closed_date > n.event_date))))
RETURN DISTINCT labels(n)[0] AS kind, coalesce(n.srid, n.ntsb_num, n.name) AS id, n.acronym AS acronym
"""

# --- API: entity pages ---------------------------------------------------------------------------------------------
REC_DETAIL = """
MATCH (r:Recommendation {srid: $srid})
OPTIONAL MATCH (r)-[:ISSUED_FROM]->(o)
OPTIONAL MATCH (r)-[ad:ADDRESSED_TO]->(org:Organization)
RETURN r {.srid, .text, .status_code, .status_label, .is_open, .closed_date, .issued_date, .issued_from, .priority,
          .times_reiterated, .letter_url, .impact_score} AS rec,
       o {.ntsb_num, .city, .state, .event_date, .fatal, .display, .is_study} AS origin, labels(o)[0] AS origin_kind,
       collect(DISTINCT {name: org.name, acronym: org.acronym, status: ad.status, label: ad.status_label}) AS owners
"""
STATUS_HISTORY = """
MATCH (:Recommendation {srid: $srid})-[h:HAD_STATUS]->(s:Status)
RETURN s.code AS code, s.label AS label, h.valid_from AS valid_from, h.valid_to AS valid_to, h.basis AS basis,
       h.exact AS exact, h.letter AS letter
ORDER BY valid_from
"""
REC_LINKED_CRASHES = """
MATCH (r:Recommendation {srid: $srid})-[:REITERATED_IN|RECLASSIFIED_IN]->(p:Report)-[:ABOUT]->(a:Accident)
WHERE NOT (r)-[:ISSUED_FROM]->(a) AND r.issued_date < a.event_date AND (r.closed_date IS NULL OR r.closed_date > a.event_date)
RETURN DISTINCT a.ntsb_num AS ntsb_num, a.city AS city, a.state AS state, a.event_date AS event_date, a.fatal AS fatal,
       p.display AS report
ORDER BY event_date
"""
ACCIDENT_DETAIL = """
MATCH (a:Accident {ntsb_num: $id})
OPTIONAL MATCH (p:Report)-[:ABOUT]->(a)
RETURN a {.ntsb_num, .event_date, .city, .state, .country, .lat, .lon, .fatal, .injury_level, .probable_cause, .in_pool,
          .partial, .family_id} AS accident, collect(DISTINCT p.display) AS reports
"""
ACCIDENT_LINKED = LINKED + """
WITH DISTINCT r, a
OPTIONAL MATCH (r)-[:ISSUED_FROM]->(o)
OPTIONAL MATCH (r)-[h:HAD_STATUS]->(s:Status)
WHERE h.valid_from <= a.event_date AND (h.valid_to IS NULL OR h.valid_to > a.event_date)
OPTIONAL MATCH (r)-[:ADDRESSED_TO]->(org:Organization)
RETURN r.srid AS srid, r.text AS text, r.issued_date AS issued, r.times_reiterated AS times_reiterated,
       r.status_code AS code_today, r.status_label AS status_today, s.code AS code_then, s.label AS status_then,
       h.valid_from AS status_since, o.ntsb_num AS origin_ntsb, coalesce(o.city + coalesce(", " + o.state, ""), o.display) AS origin_place,
       o.event_date AS origin_date, collect(DISTINCT org.acronym) AS owners
ORDER BY issued, srid
"""
ACCIDENT_MENTIONS = """
MATCH (r:Recommendation)-[:MENTIONED_IN]->(p:Report)-[:ABOUT]->(a:Accident {ntsb_num: $id})
WHERE NOT (r)-[:REITERATED_IN|RECLASSIFIED_IN]->(:Report)-[:ABOUT]->(a)
RETURN r.srid AS srid, p.display AS report ORDER BY srid
"""
OWNER_OPEN = """
MATCH (o:Organization)<-[a:ADDRESSED_TO]-(r:Recommendation)
WHERE (o.acronym = $acronym OR o.name = $acronym) AND a.status STARTS WITH 'O'
OPTIONAL MATCH (r)-[:ISSUED_FROM]->(src)
RETURN r.srid AS srid, a.status AS status, a.status_label AS label, r.issued_date AS issued, r.times_reiterated AS times_reiterated,
       substring(r.text, 0, 220) AS text, coalesce(src.city + coalesce(', ' + src.state, ''), src.display) AS origin, o.name AS owner_name
ORDER BY issued
"""
OWNER_TOTAL = """
MATCH (o:Organization)<-[:ADDRESSED_TO]-(r:Recommendation) WHERE o.acronym = $acronym OR o.name = $acronym
RETURN o.name AS name, count(DISTINCT r) AS n ORDER BY n DESC LIMIT 1
"""
MOST_REPEATED_OUA = """
MATCH (r:Recommendation {status_code: 'OUA'}) WHERE r.times_reiterated > 0
RETURN r.srid AS srid, r.times_reiterated AS times_reiterated, substring(r.text, 0, 200) AS text
ORDER BY times_reiterated DESC, srid LIMIT 5
"""
REC_ORIGINS = """
MATCH (r:Recommendation)-[:ISSUED_FROM]->(a:Accident) WHERE r.srid IN $srids
RETURN r.srid AS srid, a.ntsb_num AS ntsb_num, a.city AS city, a.state AS state, a.lat AS lat, a.lon AS lon
"""
INSIGHTS_LEADERS = """
MATCH (r:Recommendation) WHERE r.impact_score IS NOT NULL
RETURN r.srid AS srid, r.status_label AS status, r.status_code AS code, r.times_reiterated AS times_reiterated,
       r.impact_score AS impact, substring(r.text, 0, 160) AS text
ORDER BY impact DESC LIMIT 10
"""
SEARCH = """
CALL {
  MATCH (r:Recommendation) WHERE r.srid STARTS WITH $id
  RETURN 'Recommendation' AS kind, r.srid AS id, r.text AS label, r.status_code AS note, r.srid AS target ORDER BY id LIMIT 6
  UNION
  MATCH (a:Accident) WHERE a.ntsb_num STARTS WITH $id
  RETURN 'Accident' AS kind, a.ntsb_num AS id, coalesce(a.city, '') + ', ' + coalesce(a.state, '') AS label,
         toString(a.event_date) AS note, a.ntsb_num AS target ORDER BY id LIMIT 6
  UNION
  MATCH (p:Report) WHERE p.display STARTS WITH $id OPTIONAL MATCH (p)-[:ABOUT]->(a:Accident)
  WITH p, head(collect(a.ntsb_num)) AS acc
  RETURN 'Report' AS kind, p.display AS id, coalesce(p.kind, 'Report') AS label, toString(p.report_date) AS note, acc AS target ORDER BY id LIMIT 6
  UNION
  MATCH (o:Organization) WHERE toUpper(o.acronym) = $id OR toUpper(o.name) CONTAINS $id
  RETURN 'Organization' AS kind, coalesce(o.acronym, o.name) AS id, o.name AS label, null AS note, coalesce(o.acronym, o.name) AS target ORDER BY id LIMIT 6
}
RETURN kind, id, label, note, target
"""
