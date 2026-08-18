# db/_extract — source-extraction provenance

> **For day-to-day incremental updates** (adding a single article/event/source, flipping a
> status, re-verifying stale URLs), prefer `db/update.py` — it appends to the live DB,
> recomputes the affected score, and writes a `change_log` row without a full CSV rebuild.
> The scripts below are the right tool for **bulk backfills** from the original extraction
> JSONs (re-deriving sources/events/evidence/archives at scale). See README "Operating modes".

This directory holds the intermediate artifacts that produced the 2026-07-25 source
expansion and event re-linking. It is kept as **provenance** — it documents exactly
which research file each of the 105 added sources came from, and which event→source
links were grounded in the file text rather than inferred.

It is not needed to rebuild the database (`db/load.py` reads only `data/*.csv`),
but it is needed to *reproduce or audit* the re-linking decisions.

## Files

| File | Role |
|------|------|
| `2022.json`, `2023.json`, `2024.json`, `2026.json`, `gov.json`, `trade.json` | Structured output of the extraction subagents. Each has a `sources` array (full-URL sources found in the matching research notes (kept internal, not published in this repo), with title/date/publisher/confidence) and a `mappings` array (event_id → URL, recorded only when the file text actually supports the link — no fabrication). `2025.json` and a companies trace were omitted: those research files contain only bare domain names, no full URLs, so extraction returned nothing. |
| `consolidate.py` | Merges the extracts into `data/sources.csv` + `data/events.csv`. Dedups sources by URL, assigns new IDs (17+), resolves one URL per event (confidence-prioritized, with a small bias toward status-trace files for follow-up event types), keeps the existing hand-curated links, retires the bogus generic source-15 links, and applies a few manual recoveries / overrides (documented inline). DRY by default; writes only with `--apply`. |
| `link_null_events.py` | Second pass (2026-07-25): grounds 12 of the 13 previously-NULL events to real articles found by direct web search, adding 8 sources (ids 122–129). Each URL was WebFetch-verified (or, for Angop, search-confirmed) to resolve to the matching article — no fabrication. Event 80 (Banco Sol) stays NULL. DRY by default; writes only with `--apply`. |
| `expand_evidence.py` | Expands `project_evidence` (field-level provenance) from the 7 hand-curated case studies to every other project, applying the documented convention (the source behind the event that establishes each field; `observed_at` = that event's date). Fields with no sourced establishing event are **skipped**, never linked to an unrelated source — no fabrication. DRY by default; writes only with `--apply` (then re-run `load.py`). |
| `archive_sources.py` | Backfills `sources.archived_url` for dead/blocked URLs by querying the Wayback Machine availability API and recording the closest real snapshot. Source 12 (mojibake-corrupted URL) is queried via source 29's clean URL (same RFI article). URLs with no API-verifiable snapshot are left empty rather than guessed. DRY by default; writes only with `--apply`. |

## Reproducing

```
python db/_extract/consolidate.py            # dry run, prints the per-event decision report
python db/_extract/consolidate.py --apply     # writes data/sources.csv + data/events.csv
python db/_extract/link_null_events.py        # dry run, prints the grounding plan
python db/_extract/link_null_events.py --apply # adds 8 sources + grounds 12 events
python db/_extract/add_columns.py             # one-time: add last_verified/created_at/url_status/evidence_complete cols + evidence skeleton
python db/_extract/expand_evidence.py         # dry run, prints the field-evidence plan
python db/_extract/expand_evidence.py --apply  # writes project_evidence rows for the non-case-study projects
python db/_extract/archive_sources.py         # dry run, prints the archive-snapshot plan
python db/_extract/archive_sources.py --apply  # writes sources.archived_url (Wayback-verified snapshots only)
python db/load.py                            # rebuild db/investment_tracker.db (FK + score-consistency gates)
python db/calculate_scores.py --update-csv   # repopulate execution_score AND sync data/projects.csv
python db/verify_sources.py --apply          # URL liveness → stamps sources.last_verified + url_status
python db/verify_invariants.py               # 55 structural invariant checks (+ known-open-issue warnings)
python db/verify_snapshot.py                 # snapshot drift + article pin
python db/query.py --summary                 # read-only JSON access layer (workflow-integration leg)
```

## Rules encoded in consolidate.py

- Existing sources 1–14 and 16 are kept (hand-curated Phase-0 set).
- Duplicate id 11 (shared source 15's URL) is dropped; its event remaps to 15.
- Events on the generic source 15 are re-linked to the agent's grounded URL when one
  exists, else set to NULL (never fabricated).
- `MANUAL_URL_MAP` — 4 events (35, 36, 61, 79) whose source is explicitly attributed
  in `status-trace-gov.md` but was not auto-mapped; mapped by hand to that URL.
- `MANUAL_KEEP` — 2 events (14, 17) where the agent's mapping was clearly wrong; the
  original specific source is kept.
- `MANUAL_PUBLISHER` — 4 events (26–29) grounded to a named publisher in
  `status-trace-companies.md` but with no full article URL; a no-URL publisher source
  record is created so the attribution is recorded rather than NULLed.

See `docs/data-lineage.md` § "Source re-linking applied 2026-07-25" for the outcome.