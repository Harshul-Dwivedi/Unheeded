# Data

## Sources (all public, US federal records, public domain)
NTSB CAROL: https://data.ntsb.gov/carol-main-public/keyword-search
Reference pages: CAROL help https://www.ntsb.gov/Pages/CAROL.aspx · status definitions
https://www.ntsb.gov/safety/safety-recs/Pages/Status-Explanation.aspx · recommendation field descriptions
https://www-s.ntsb.gov/investigations/Pages/field-descriptions.aspx
Do not use the login.gov beta API (rule: no login-protected data). The public CSV/JSON downloads are enough.
CAROL caps a download at 10,000 rows; filter so results stay under it.

## Files in `data/raw/` (verified 9 Oct 2026)
| File | How exported | Rows | Use |
| --- | --- | --- | --- |
| carol_recommendations_data.json | Safety Recommendations tab, Aviation, no dates | 6,109 | Core recommendations file. 5,964 `Mode = Aviation` (`A-`); 145 are I/M/R/H with Aviation only in `TopicMode` — keep `Srid` starting `A-` |
| carol_investigations_data.json | Investigations tab, Mode = Aviation, Overall Recommendation Status = every status | 1,133 | Accidents that produced recommendations (Oct 1965–Feb 2025) |
| carol_aviation_investigations_data.json | Investigations tab, Mode = Aviation (under cap) | 9,530 | Recent accident pool, 1 Jan 2021–11 Sep 2026, Aviation only. `ReportNumber` set on only 11 |

Planning used an older set (6 Oct: 1,112-row investigations file, 10,000-row all-modes capped pool); superseded.

Join keys: `Notations[].NtsbNum` (recommendation) ↔ `NTSBNumber` (investigation). `Notations[].ReportNum`
("AIR-26-05") ↔ `ReportNumber` ("AIR2602") after normalising to `[A-Z0-9]`.

## Recommendation record (top-level fields that matter)
`Srid` (A-16-035) · `Subject` (full text, starts "TO THE …:") · `OverallStatus` code (OUA, OAA, ORR, OAAR, OAR,
CAA, CUA, CAAA, CRE, CNLA, CS…) · `OpenClosed` · `DateClosed` · `Priority` · `MostWanted` · `IsReiterated`,
`TimesReiterated` · `Keywords` · `SafteyRecCodings[]` (Tier1Name/Tier2Name, sparse) · `GreenSheet` (official
letter PDF URL) · `Mode` · `Notations[]` {NtsbNum, ReportNum, AccidentDate, City, State, DateIssued, DateAdopted}
· `Addressees[]` {Name, AddressAcronym, AddresseeStatus, OpenClosedAddressee, Correspondence[]}.
`Correspondence[]` {CorrespondenceDate, CorrespondenceType, IsFromNtsb, ResponseSummary}. Types seen:
Transmittal Letter, Response (addressee), NTSB Response, Recommendation Reiteration, Recommendation Mention,
Report Reclassification, Informal Staff Update, Closing letter, etc.

## Investigation record (fields that matter)
`NTSBNumber` · `ReportNumber` · `ReportType` · `InvestigationClass` · `EventDateTimeUTC` · `ReportDate` ·
`BoardMeetingDate` · `City/State/Country` · `Latitude/Longitude` · `HighestInjuryLevel` · `FatalInjuryCount`
· `ProbableCause` · `AnalysisNarrative` · `hasSafetyRecommendation` · `Vehicles[]` {Findings[] {FindingCode,
finding_Tier1Name…Tier4Name, FindingReportText, InProbableCause}, Events[] {CICTT group, phase}}.
Make/Model/Operator fields are NOT populated in the 10k pool export; aircraft type must come from text or the
recommendations' letters where needed.

## Known quirks (handle in the loader)
1. **Correspondence is duplicated ~7× per entry** in the export. Deduplicate on (date, type, first 120 chars). Overall 43,373 → 35,273 (heavy letters like A-16-035/FAA 126 → 18). 612 letters have no date.
2. `DateIssued`, `DateAdopted` = `0001-01-01` everywhere; `Year` = 0. **Issue date rule (revised 10 Oct):** the
   earliest of the origin investigation's `BoardMeetingDate`/`ReportDate`, the earliest correspondence date or
   `DateClosed`, and 31 Dec of the ID year, ignoring dates before 1 Jan of the ID year. Each candidate is an upper
   bound: urgent recs go out months before the final report (A-25-001: letter 11 Mar 2025, report Jan 2026), no letter
   precedes its rec, and the ID year is the issue year. The old rule ("report date first") put 2,232 of 4,211 recs in
   the wrong year. Source mix now: letter 3,322 · report 1,496 · ID year 1,146 (an upper bound, day unknown).
3. 35 recommendations come from safety studies: `AccidentDate` = `0001-01-01`, no City. Treat origin as a Report, not an Accident.
4. 435 origin investigations are not in the 1,133 file (legacy numeric IDs like `80386`, foreign `…RA…`, studies).
   Their date/city/report number are in `Notations`; create Accident nodes from that with `partial = true`.
5. Dates: recommendations use local dates, investigations use UTC. Compare on date only and expect ±1 day drift
   (the DC midair appears as 2025-01-29 vs 2025-01-30). Never link a recommendation to its own origin accident.
6. The 10k pool includes highway (`HWY`), pipeline (`PLD`), marine etc. Filter `Mode == "Aviation"`.
7. Structured `Findings` codes exist mostly for accidents after ~2008 (115 of the 1,112 origin accidents in the 6 Oct file; not re-counted).
   Probable-cause text: none for 1960s–70s, ~90% from the 1990s on.
8. Status history is only partly structured: current status + close date are fields; earlier classifications
   are inside NTSB letters ("we classify … Open—Unacceptable Response"). Parse what you can; a small LLM pass is acceptable.
9. Some recommendations go to non-FAA bodies (NWS, operators, EASA, Transport Canada). Keep them; the owner is a node.
10. Multi-aircraft events: Notations cite `CEN19MA141AB`, the investigation files hold `CEN19MA141` (41 such pairs). Resolve to the base id.
11. Special investigations/studies are recognisable by type code `SS`/`SR`/`SP` (`DCA20SP001`); some carry a date in Notations. Treat as Report, never as a crash.
12. Supersession lives in `Subject`: "(Supersedes A-91-029; Superseded by A-24-4 through -6)". Split clauses on `;`, expand ranges, pad to 3 digits → 560 directed pairs.

## The ONE definition (use verbatim in README and method page)
A recommendation is a **linked warning** for a crash when all three hold:
1. The NTSB **repeated or reclassified** it in that crash's own report (correspondence type
   `Recommendation Reiteration` or `Report Reclassification`, referencing the report number). A `Recommendation Mention` alone does not count.
2. It was **issued before the crash** (issue date rule above).
3. It was **still open on the day of the crash** (`DateClosed` null or after the crash date).
Result on the current exports: 16 crashes, 54 links, 48 killed (`data/derived/gold_links_strict_definition.json`).
Nome has 8 linked warnings (A-13-012, A-13-013, A-16-034, A-16-035, A-22-021, A-22-022, A-24-017, A-24-018).
A-21-049 is a mention only → not linked. Togiak 2016 is NOT linked to A-16-035 (issued after it). Calabasas 2020 is NOT linked to A-20-029 (issued 2 Jun 2020, after it).

## Key facts for the demo (verified)
- A-16-035: OUA; reiterated 7×; letters 2017–2026; unacceptable since 2020-06-08 (AAR-20-01 letter);
  linked crashes Teterboro 2017 (2), Zaleski 2019 (3), Kekaha 2019 (7), Calabasas 2020 (9), Nome 2025 (10) = 31 killed.
- A-13-012/013 from CEN11FA599 Mosby MO 2011-08-26 (4 killed), AAR-13-02. A-16-034/035/036 from CEN16MA036 Akron OH 2015-11-10 (9 killed), AAR-16-03.
- A-22-021/022 from study DCA22SS002 (AIR-22-07, Alaska icing forecasts); closed acceptable 2026-07-30.
  A-24-017 (OAA) and A-24-018 (OUA) from special investigation DCA23SR001 (AIR-24-03, Part 135; Notation date 2024-07-24, issued 2024-08-09 per transmittal letter and published date).
- FAA: 5,236 aviation recommendations received; open 228 = OUA 71 + OAA 114 + ORR 26 + OAAR 13 + OAR 4.
  OUA by issue period: 2000–04 1 · 2005–09 2 · 2010–14 9 · 2015–19 13 · 2020–24 35 · 2025–26 11.
- Most-reiterated OUA: A-13-013 8×, A-16-035 7×, A-16-034 6×, A-13-012 5×, A-24-009 2× (25-hour CVR, replaces A-18-31).
- Linked crashes (strict): Memphis TN 2003 (0,1; A-03-016, AAR-05-01; added 10 Oct with the issue-date fix), District Heights MD 2008 (4 killed, 1 link), New York 2015 (0,1), Frisco CO 2015 (1,2),
  Togiak AK 2016 (3,2), Chicago 2016 (0,1), Teterboro NJ 2017 (2,6), Zaleski OH 2019 (3,4), Trinity Bay TX 2019 (3,6),
  Kekaha HI 2019 (7,8), Calabasas CA 2020 (9,4), Queens NY 2023 (0,3), Austin TX 2023 (0,2), Portland OR 2024 (0,4), Nome AK 2025 (10,8), and Ketchikan AK 2019 (6,1; A-16-036, AAR-21-04; added 9 Oct). 48 killed in total.
- Nome coordinates 64.35 N, 164.28 W; report AIR-26-05; probable cause begins "The pilot's inadequate airspeed management…".

## Map
`design/assets/docket-light.svg` / `docket-dark.svg`: US states, Albers USA projection (d3 `geoAlbersUsa().scale(1300).translate([487.5,305])`,
viewBox 975×610), from `us-atlas` (Census boundaries, public domain). Project points with the same projection
(`data/scripts/render_us_map.mjs`) and place pins as percentages of width/height. Nome = (6.31%, 84.77%).
