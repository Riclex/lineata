# Angola Investment Execution Database — Data Model

## Overview

The database tracks announced investment projects in Angola and their execution outcomes over time. Every project is a living record with an event timeline, linked organizations, and verified sources.

## Tables

### projects

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (PK) | Unique identifier (slug-based, e.g. `luanda-glass-factory`) |
| title | TEXT | Project name |
| sector | TEXT | Primary sector (Agriculture, Manufacturing, Mining, Energy, Infrastructure, Real Estate, Tourism, Logistics, Telecom, Finance, Health, Education, Other) |
| subsector | TEXT | More specific category |
| description | TEXT | Brief project description |
| country | TEXT | Country (Angola for all Phase 0/1 records) |
| province | TEXT | Angolan province (Luanda, Benguela, Huambo, Cabinda, etc.) |
| municipality | TEXT | Municipality if known |
| coordinates | TEXT | "lat, lon" if available |
| status | TEXT | Enum: announced, mou_signed, financed, under_construction, delayed, suspended, restarted, operational, completed, cancelled, unknown |
| announced_value | REAL | Announced investment value |
| currency | TEXT | ISO currency code (USD, EUR, AOA, CNY) |
| estimated_jobs | INTEGER | Announced job creation |
| expected_completion | TEXT | Expected completion date (YYYY or YYYY-MM) |
| actual_completion | TEXT | Actual completion date (YYYY or YYYY-MM), NULL if not yet |
| execution_score | INTEGER | Calculated 0–100 (see scoring below) |
| filda_edition | TEXT | FILDA edition the project was announced at (e.g. "2023") |
| last_verified | TEXT | YYYY-MM-DD a human last checked this project's status against sources |
| evidence_complete | INTEGER | 1 = scored (default); 0 = tracked but NOT scored (no click-through evidence — see `docs/data-lineage.md` "Event 80 (Banco Sol)"). Scored 0 and excluded from published aggregates. |
| created_at | TEXT | Record creation timestamp |
| updated_at | TEXT | Last update timestamp (advanced by `trg_projects_updated` on real edits; see `db/update.py`) |

### organizations

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (PK) | Unique identifier |
| name | TEXT | Organization name |
| type | TEXT | Enum: company, government, state_owned_enterprise, foreign_investor, contractor, financier |
| country | TEXT | Headquarters country |
| parent_org_id | TEXT (FK) | Parent organization if subsidiary |
| aliases | TEXT | JSON array of alternative names/ spellings |
| description | TEXT | Brief description |
| created_at | TEXT | |
| updated_at | TEXT | |

### project_organizations

Junction table — many-to-many between projects and organizations with a role.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (PK) | |
| project_id | TEXT (FK) | |
| organization_id | TEXT (FK) | |
| role | TEXT | Enum: promoter, investor, contractor, financier, partner, operator |
| created_at | TEXT | |

### events

Every project gets multiple events forming a timeline.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (PK) | |
| project_id | TEXT (FK) | |
| event_type | TEXT | Enum: announcement, mou, financing, groundbreaking, construction, delay, suspension, restart, completion, expansion, closure, ownership_change |
| event_date | TEXT | When the event occurred (YYYY-MM-DD, YYYY-MM, or YYYY) |
| description | TEXT | What happened |
| source_id | INTEGER (FK) | Link to sources table |
| created_at | TEXT | |

### sources

Every claim is linked to a source.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (PK) | |
| title | TEXT | Article/ document title |
| url | TEXT | Original URL |
| date | TEXT | Publication date |
| publisher | TEXT | Publishing organization |
| archived_url | TEXT | Web Archive / archive.today link |
| confidence | TEXT | Enum: high, medium, low |
| last_verified | TEXT | YYYY-MM-DD the URL was last checked to resolve (stamped by `db/verify_sources.py`) |
| url_status | TEXT | 'alive' \| 'dead' \| 'blocked' \| 'n/a' (empty URL / publisher-only) |
| created_at | TEXT | |

### project_evidence

Field-level provenance — backs a project's own fields (status, announced_value, estimated_jobs, actual_completion) to a click-through source, not just its events. Operationalises the goal's "don't score projects unless someone can click through the evidence." Expanded by `db/_extract/expand_evidence.py`; appended to incrementally by `db/update.py add-evidence`.

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (PK) | |
| project_id | TEXT (FK) | |
| field | TEXT | Which project field this backs (status, announced_value, estimated_jobs, actual_completion, ...) |
| value | TEXT | The value as observed in the source |
| source_id | INTEGER (FK) | Link to sources table |
| observed_at | TEXT | YYYY-MM-DD the value was observed |
| created_at | TEXT | |

UNIQUE(project_id, field, source_id) — a field may be backed by multiple sources (append-only); changing a value requires a new source, not an overwrite.

### change_log

Append-only audit trail of every incremental mutation made through `db/update.py` (and the `load-seed` / `export-csv` checkpoint markers). Loaded from `data/change_log.csv` by `load.py`, so the audit history survives rebuilds and is git-diffable. See `docs/data-lineage.md` "Incremental update layer applied 2026-07-27".

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER (PK) | |
| ts | TEXT | Timestamp (defaults to now) |
| operation | TEXT | 'add-source' \| 'add-event' \| 'add-evidence' \| 'set-status' \| 'relink-event' \| 'relink-evidence' \| 'reverify' \| 'export-csv' \| 'load-seed' |
| target_table | TEXT | 'sources' \| 'events' \| 'project_evidence' \| 'projects' \| 'db_meta' |
| target_id | TEXT | Row id of the target (TEXT covers both TEXT and INTEGER PKs) |
| payload_json | TEXT | JSON snapshot of what was written/changed |
| source_url | TEXT | The `--source-url` that authorized the change (NULL for reverify/export/load) |
| note | TEXT | |

### db_meta

Key/value metadata table. Holds two rows: `last_exported_at` (the checkpoint
watermark) and `score_version` (the formula-version stamp, see
`docs/scoring-methodology.md` § Versioning). `db/export_csv.py` stamps
`last_exported_at`; `db/load.py` stamps `score_version` on every rebuild;
`db/verify_invariants.py` asserts the `score_version` row matches
`calculate_scores.SCORE_VERSION`. `load.py`'s staleness guard refuses a rebuild
if `change_log` has mutation rows newer than `last_exported_at` (would silently
lose uncheckpointed DB edits) unless `--force` is passed.

| Column | Type | Description |
|--------|------|-------------|
| key | TEXT (PK) | 'last_exported_at' or 'score_version' |
| value | TEXT | datetime('now') of the last successful checkpoint, or the SCORE_VERSION string |

## Execution Score Calculation

`execution_score` is a 0–100 integer computed by `db/calculate_scores.py` from
each project's `status`, its event timeline, evidence signals, delay, and
penalties. The authoritative formula, component tables, and worked examples
that reproduce against the script live in **[docs/scoring-methodology.md](scoring-methodology.md)**.

In short:

```
Score = Base(status) + Event Points (≤30) + Evidence Bonus (≤10)
        − Delay Penalty − Status Penalty − Only-Announcement Penalty
```

clamped to [0, 100]. The score stored in `data/projects.csv` is a snapshot that
`db/load.py` verifies against this formula on every rebuild (and
`db/calculate_scores.py --update-csv` refreshes it after data edits).

## Aggregation Views

### By Company
Projects, Completed, Execution Score

### By Country (investor origin)
Projects, Completed

### By Sector
Completion rate (%)

### By Province
Execution statistics

## Design Notes

- Positions around "execution", not "failure" — a completed project and a stalled project are both valuable data points
- The customer wants to know: what is the probability that an announced investment becomes a real operating asset?
- Entity resolution is critical: the same company may appear under different names, subsidiaries, or after mergers. The `aliases` field and `parent_org_id` support this.
- Every claim must have a source with a confidence level. No unsourced assertions.