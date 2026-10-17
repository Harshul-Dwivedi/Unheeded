"""Read-only graph tools (docs/architecture.md). The MCP server wraps these one-to-one.

Every call: read-only query, server-side timeout, LIMIT cap, returns plain data plus `ms` (server time, summed).
Facts come only from these results; agents never state a date, status or id a tool did not return.
"""
import re

from unheeded import queries as Q

TIMEOUT_MS = 1000
MAX_ROWS = 200
# default rule weights for unstated-link candidates; the Review queue updates them per workspace (Phase 3)
DEFAULT_WEIGHTS = {"text": 1.0, "findings": 0.5, "events": 0.3, "family": 0.2, "seed": 0.5}
NTSB_ID = re.compile(r"^[A-Z]{3}\d{2}[A-Z]{2}\d{3}[A-Z]*$")
# too common in place names to identify a crash: "Bering Air ... Nome" must not match "Hida Air Park" or "Buenos Aires"
GENERIC_PLACE = set("""air airport airpark park field lake lakes city county beach river island isle bay mount mountain
new north south east west saint st fort port valley creek spring springs hill hills heights international regional
municipal national station center centre falls rock point grove village town township harbor harbour""".split())


class Run:
    """Collects query time across the calls one tool makes."""

    def __init__(self, g):
        self.g, self.ms, self.queries = g, 0.0, []

    def rows(self, cypher, **params):
        res = self.g.ro_query(cypher, params, timeout=TIMEOUT_MS)
        self.ms += res.run_time_ms
        self.queries.append(" ".join(cypher.split()))  # the exact Cypher, for Step logs and the UI
        return res.result_set

    def dicts(self, cypher, **params):
        res = self.g.ro_query(cypher, params, timeout=TIMEOUT_MS)
        self.ms += res.run_time_ms
        self.queries.append(" ".join(cypher.split()))  # the exact Cypher, for Step logs and the UI
        names = [h[1] for h in res.header]
        return [dict(zip(names, r)) for r in res.result_set]

    def done(self, **out):
        return {**out, "ms": round(self.ms, 2), "queries": self.queries}


def cap(limit):
    return max(1, min(int(limit), MAX_ROWS))


def resolve_accident(g, text, year=None, limit=5):
    """NTSB number, or a place name ('Nome', 'Kekaha Hawaii') with an optional year."""
    run = Run(g)
    t = text.strip().upper()
    if NTSB_ID.match(t):
        return run.done(matches=[m for m in run.dicts(Q.ACCIDENT_BY_ID, id=t) if m["ntsb_num"]])
    words = [w for w in re.findall(r"[A-Za-z]{2,}", text) if w.lower() not in GENERIC_PLACE] or re.findall(r"[A-Za-z]{2,}", text)
    if not words:
        return run.done(matches=[])
    q = "|".join(f"%{w}%" if len(w) > 4 else w for w in words)  # any word; %x% = fuzzy (1 edit) in RediSearch syntax
    matches = run.dicts(Q.ACCIDENT_BY_PLACE, q=q, year=year, limit=cap(limit) * 3)
    wanted = {w.lower() for w in words}
    exact = lambda m: len(wanted & {w.lower() for w in re.findall(r"[A-Za-z]{2,}", f"{m.get('city') or ''} {m.get('state') or ''}")})
    matches.sort(key=lambda m: -exact(m))  # stable: exact place words first ("McKinney" before fuzzy "McKinley"), then score
    return run.done(matches=matches[:cap(limit)])


def find_open_warnings(g, ntsb_num):
    run = Run(g)
    return run.done(ntsb_num=ntsb_num, warnings=run.dicts(Q.FIND_OPEN_WARNINGS, id=ntsb_num))


def prove_no_linked_warning(g, ntsb_num):
    run = Run(g)
    rows = run.rows(Q.PROVE_NO_LINKED_WARNING, id=ntsb_num)
    reports, repeated = (rows[0][2], rows[0][3]) if rows else ([], [])
    # reports = [] means there was nothing to search: absence there is not evidence of anything
    return run.done(ntsb_num=ntsb_num, found=bool(rows), no_linked_warning=bool(rows) and rows[0][1],  # fail closed
                    reports=reports, has_report=bool(reports), repeated_outside_definition=repeated if rows and rows[0][1] else [],
                    edge_types_searched=["REITERATED_IN", "RECLASSIFIED_IN"], path="Recommendation->Report->Accident",
                    hop_budget=2, conditions=["issued before the crash", "open on the crash date", "not its own origin"])


def status_on_date(g, srid, date):
    """date as yyyymmdd int."""
    run = Run(g)
    rows = run.dicts(Q.STATUS_ON_DATE, srid=srid, date=int(date))
    return run.done(srid=srid, date=int(date), status=rows[0] if rows else None)


def trace_chain(g, srid):
    run = Run(g)
    after = run.rows(Q.SUPERSEDED_BY_CHAIN, srid=srid)
    before = run.rows(Q.SUPERSEDES_CHAIN, srid=srid)
    return run.done(srid=srid, superseded_by=[{"srid": s, "hops": h} for s, h in after],
                    supersedes=[{"srid": s, "hops": h} for s, h in before], repeats=run.dicts(Q.REPEATS, srid=srid))


def owner_backlog(g, acronym, limit=MAX_ROWS):
    run = Run(g)
    counts = run.dicts(Q.OWNER_COUNTS, acronym=acronym)
    rows = run.dicts(Q.OWNER_BACKLOG, acronym=acronym, limit=cap(limit))
    return run.done(owner=acronym, counts=counts, total_open=sum(c["n"] for c in counts), open=rows,
                    truncated=len(rows) < sum(c["n"] for c in counts))


def _scrubbed(rows, key):
    """Letter text is official correspondence and names its signatories: scrubbed here, the one place every reader
    (agents, MCP clients, the browser's step log) goes through."""
    from unheeded.gate import scrub_names  # local: gate imports agents, which imports this module
    return [{**r, key: scrub_names(r.get(key))} for r in rows]


def get_letters(g, srid, limit=MAX_ROWS):
    run = Run(g)
    return run.done(srid=srid, letters=_scrubbed(run.dicts(Q.LETTERS, srid=srid, limit=cap(limit)), "summary"))


def features(signals):
    """Signal values on a 0..1 scale: text similarity, shared findings (cap 5), shared events (cap 3), same family,
    seed = similarity to a crash the workspace already confirmed for this pair's rec (memory-seeded retrieval).
    Shared by ranking (score) and by learning the weights (memory.fit_weights)."""
    return {"text": signals.get("text", 0), "findings": min(signals.get("findings", 0), 5) / 5,
            "events": min(signals.get("events", 0), 3) / 3, "family": signals.get("family", 0), "seed": signals.get("seed", 0)}


def score(signals, weights):
    """Plain-code ranking: weighted sum of features()."""
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    return sum(w[k] * v for k, v in features(signals).items())


def find_candidates(g, ntsb_num=None, srid=None, weights=None, seeds=(), limit=10, k=6000):
    """Unstated links, always unverified. Crash -> recs open on its date, or rec -> later pool crashes.

    Crash -> recs: text similarity is exact cosine over the recs open on the crash date (queries.CRASH_TEXT).
    Rec -> crashes: text KNN asks for k=6000 because the vector index filters after the top k (FALKORDB-ISSUES #7).
    weights: the workspace's learned rule weights (memory.weights); seeds: its confirmed (srid, ntsb_num) pairs.
    """
    if bool(ntsb_num) == bool(srid):
        raise ValueError("pass exactly one of ntsb_num or srid")
    run = Run(g)
    if ntsb_num:
        p, official = {"id": ntsb_num}, set(run.rows(Q.OFFICIAL_SRIDS, id=ntsb_num)[0][0])
        text, shared, family, seeded = Q.CRASH_TEXT, Q.CRASH_SHARED, Q.CRASH_FAMILY, Q.CRASH_SEEDS
    else:
        p, official = {"srid": srid}, set(run.rows(Q.REC_OFFICIAL, srid=srid)[0][0])
        text, shared, family, seeded = Q.REC_TEXT, Q.REC_SHARED, Q.REC_FAMILY, Q.REC_SEEDS
    sig = {}
    seeds = [{"s": s, "c": c} for s, c in seeds][:MAX_ROWS]
    for name, q in (("text", text), ("findings", shared % ("HAS_FINDING", "HAS_FINDING")),
                    ("events", shared % ("HAD_EVENT", "HAD_EVENT")), ("seed", seeded)):
        if name == "seed" and not seeds:
            continue
        for key, v in run.rows(q, k=k if name == "text" else 200, seeds=seeds, **p):
            if key not in official:
                sig.setdefault(key, {})[name] = round(v, 4)
    for key, v in run.rows(family, ids=list(sig), **p):
        sig[key]["family"] = v
    ranked = sorted(({"id": key, "score": round(score(s, weights), 4), "signals": s} for key, s in sig.items()),
                    key=lambda c: (-c["score"], c["id"]))
    return run.done(target=ntsb_num or srid, kind="recommendation" if ntsb_num else "accident", verified=False,
                    weights={**DEFAULT_WEIGHTS, **(weights or {})}, seeds_used=len(seeds),
                    excluded_official=sorted(official), candidates=ranked[:cap(limit)], considered=len(ranked))


def similar_cases(g, ntsb_num, limit=10):
    run = Run(g)
    return run.done(ntsb_num=ntsb_num, similar=run.dicts(Q.SIMILAR_CASES, id=ntsb_num, k=cap(limit) + 1)[:cap(limit)])


def explain_path(g, srid, ntsb_num, max_len=4):
    run = Run(g)
    rows = run.dicts(Q.EXPLAIN_PATH, srid=srid, id=ntsb_num, max_len=min(int(max_len), 6))
    return run.done(srid=srid, ntsb_num=ntsb_num, path=rows[0] if rows else None)


def get_sources(g, ids):
    """Recommendation ids -> letter PDF; report numbers -> crash; letter ids -> date/type/excerpt."""
    run = Run(g)
    ids = list(ids)[:MAX_ROWS]
    recs = run.dicts("MATCH (r:Recommendation) WHERE r.srid IN $ids RETURN r.srid AS srid, r.letter_url AS letter_url", ids=ids)
    reports = run.dicts("MATCH (p:Report) WHERE p.report_num IN $ids OR p.display IN $ids OPTIONAL MATCH (p)-[:ABOUT]->(a:Accident) "
                        "RETURN p.display AS report, p.report_date AS report_date, collect(a.ntsb_num) AS accidents", ids=ids)
    letters = _scrubbed(run.dicts("MATCH (l:Letter) WHERE l.id IN $ids RETURN l.id AS id, l.date AS date, l.type AS type, "
                                  "substring(l.summary, 0, 300) AS excerpt", ids=ids), "excerpt")
    return run.done(recommendations=recs, reports=reports, letters=letters)


def alert_reach(g, srid, max_level=2):
    """Watch mode: recommendations, crashes and organisations a change to `srid` reaches (BFS, outgoing edges)."""
    run = Run(g)
    return run.done(srid=srid, max_level=int(max_level), reached=run.dicts(Q.ALERT_REACH, srid=srid, max_level=min(int(max_level), 4)))
