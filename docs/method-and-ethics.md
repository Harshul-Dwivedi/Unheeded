# Method, wording and ethics

## Wording rules (apply in UI, briefs, README, video)
- Use the NTSB's terms: "rated unacceptable by the NTSB", "open — acceptable response", "closed — acceptable action".
  Do not write "ignored by the FAA" in the interface. The product name carries the editorial edge; the record stays neutral.
- "Linked" means the NTSB connected the recommendation to the crash in its own report. Every answer and brief ends
  with: "A linked warning does not establish that acting on it would have prevented the crash."
- Never name or describe individuals (pilots, officials, victims). Organisations, recommendations, accidents and
  reports only. The gate blocks any sentence naming a person.
- Deaths are stated as counts with the NTSB's figure ("10 killed"), never dramatised.
- Every number carries a source (record id, field, or query) reachable from the UI ("How we know these numbers").

## Official vs proposed
Solid = NTSB record (graph `ntsb`). Dashed = agent proposal, unverified (workspace graph). Proposals never count
towards an answer: they appear only under a labelled "Unverified proposals" heading, and a 
confirmed one is labelled as confirmed by a reviewer, not by the NTSB.

## What the agents may do
✓ read-only graph queries with time and row limits · ✓ propose connections, marked unverified
✕ publish without human approval · ✕ present an unverified proposal as a finding· ✕ name individuals · ✕ search the web

## Known limits (publish these)
- Issue dates (revised rule, docs/data.md): earliest of origin report/Board date, first letter, DateClosed, 31 Dec of the ID year.
  Sources: first letter 3,322 · report 1,496 · ID year only 1,146 (an upper bound). 133 recommendations come from studies or special investigations.
- Probable-cause text missing for most pre-1990 accidents; structured finding codes dense only from ~2008.
- Status history is parsed from NTSB classification letters by plain code; on recommendations with such a letter, the last one matches the current status 89% of the time.
- The export repeats letters several times; duplicates are removed before counting.
- US aviation only; a few foreign-addressed recommendations exist and are kept as records.
- The record is as of the export date; the NTSB updates CAROL continuously.

## Status mark legend
■ Open, rated unacceptable (OUA) · □ Open (ORR awaiting response, OAA acceptable, OAAR/OAR alternate) ·
— Closed (CAA acceptable action, CAAA acceptable alternate, CUA unacceptable action, CRE reconsidered, CNLA no longer applicable, CS superseded).
