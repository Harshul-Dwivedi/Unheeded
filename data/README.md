# Data

`raw/` holds the three public NTSB CAROL exports the graph is built from (US federal records, public domain), downloaded
9 Oct 2026 from https://data.ntsb.gov/carol-main-public/keyword-search. `carol-search-form-screenshot.png` shows the export
settings. The one definition of a linked warning is in [`docs/method-and-ethics.md`](../docs/method-and-ethics.md).

| Path | What |
| --- | --- |
| `raw/carol_recommendations_data.json` | 6,109 recommendations (5,964 aviation) with full letter threads |
| `raw/carol_investigations_data.json` | 1,133 investigations that produced recommendations |
| `raw/carol_aviation_investigations_data.json` | 9,530 aviation investigations, 2021–2026 (the crash pool) |
| `derived/gold_links_strict_definition.json` | the answer key: 16 crashes, 54 linked warnings (Eval 1, Eval 2) |
| `derived/linked_crashes_loose_definition.json` | the looser early definition, kept for comparison only |
| `scripts/gold_links.py` | rebuilds the answer key from the raw files, independently of the loader |
| `scripts/render_us_map.mjs` | renders the Albers USA base maps in `web/public/` |

Rebuild the answer key:

```bash
python data/scripts/gold_links.py data/raw/carol_recommendations_data.json data/raw/carol_investigations_data.json \
  data/raw/carol_aviation_investigations_data.json > data/derived/gold_links_strict_definition.json
```
