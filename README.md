# Angola Investment Execution Database

A PitchBook for announced investments in Angola. Tracks what actually happened to projects announced at FILDA and other sources — not just what was announced.

> **New here?** Start with [`docs/getting-started.md`](docs/getting-started.md) — a 10-step walkthrough from clone to your first round-trip change. Then read `InvestmentExecutionDatabase-goal.md` for the product vision and `docs/scoring-methodology.md` for the execution-score formula.

## Concept

A spreadsheet of "projects announced at FILDA" has limited value. A database that answers "Who actually delivers in Angola?" is valuable.

Every project becomes an event timeline:

```
Luanda Glass Factory
2012  Announced at FILDA
2013  MoU signed
2014  Construction started
2015  Funding suspended
2018  Chinese partner exited
2021  Project restarted
2025  Operating at 35% capacity
```

That's intelligence.

## Data Sources

- FILDA (Feira Internacional de Luanda)
- AIPEX investment announcements
- Presidential announcements
- Council of Ministers
- Ministry of Industry / Agriculture / Mines
- Sonangol, ENDIAMA
- Provincial governments
- PPP concessions
- Major foreign investment announcements
- Chinese-funded projects
- World Bank, AfDB, IFC
- Press releases & company announcements

## Data Model

### Projects
id, title, sector, subsector, description, country, province, municipality, coordinates, status, announced_value, currency, estimated_jobs, expected_completion, actual_completion, execution_score

### Organizations
Company, Government entity, State-owned enterprise, Foreign investor, Contractor, Financier

### Events
Announcement, MoU, Financing, Groundbreaking, Construction, Delay, Suspension, Restart, Completion, Expansion, Closure

### Sources
Title, URL, Date, Publisher, Archived copy, Confidence

## Operating Modes

Maintenance is **event-driven**, not a full CSV rebuild. Three modes:

- **Seed (rare):** `python db/load.py` — rebuild `db/investment_tracker.db` from the `data/*.csv` checkpoints. Used for a fresh setup or after a checkpoint refresh. Runs the FK + score-consistency gates. **Refuses** if the live DB has uncheckpointed `change_log` mutations (would silently lose them); pass `--force` to discard.
- **Append (day-to-day):** `python db/update.py <subcommand> --apply` — append-only mutator. Each call requires a verifiable `--source-url` (no fabrication), recomputes only the affected project's score, and writes one `change_log` audit row. Subcommands: `add-source`, `add-event`, `add-evidence`, `set-status`, `relink` (re-link an existing event/evidence row to a grounded source, or `--clear` to NULL an ungrounded link), `reverify`. Dry-run by default.
- **Checkpoint (after each append session):** `python db/export_csv.py --apply` — refresh `data/*.csv` from the DB so `load.py` can reproduce it, and stamp the `db_meta.last_exported_at` watermark. Exports the computed (formula) score, not the stored column.

The discipline: **always `export_csv.py --apply` after `update.py --apply`** before rebuilding, or the staleness guard will refuse the rebuild.

## Monitoring

- **`python db/health.py`** — one-command gate: unit tests → round-trip rebuild → article↔DB contract → source URL liveness → doc-figure drift. Exits non-zero on the first failure, so it can gate a publish. `--fast` (tests + verify.py only) is for the pre-commit hook; `--no-network` skips URL liveness.
- **`python db/verify_docs.py`** — scans `docs/*.md` + `README.md` for cited numbers (source/event counts, linked/NULL, avg score, verify.py check count, scoring-methodology worked examples) and flags any that drift from the DB.
- **`python db/changelog.py`** — read-only digest of the `change_log` audit trail: checkpoint status, mutation breakdown, score movers, new sources, and the "only Banco Sol is unsourced" invariant. `--since YYYY-MM-DD` / `--movers` filter.

## Phased Rollout

- **Phase 0 (thin slice):** FILDA 2023 — collect every identifiable announcement, trace each one
- **Phase 1 (5 years):** 2022–2026
- **Phase 2 (backfill):** 2005–2021

## Product Roadmap

- v1: Search (by company → projects, status, timeline, investment, partners)
- v2: Company pages (e.g. China Railway → projects in Angola, completed/delayed/cancelled, execution rate)
- v3: Country pages (e.g. Portugal → total announced, completed, under construction, cancelled, avg delay)
- v4: Province pages (Luanda, Benguela, Huambo → execution statistics)
- v5: Maps (every project plotted, filter by sector/value/status/investor/province)
- v6: AI (natural language queries + entity resolution across name changes/subsidiaries/mergers)

## Directory Structure

```
FILDA Investment Tracker/
├── README.md                       # This file
├── InvestmentExecutionDatabase-goal.md  # Product vision / the "why" behind the design
├── docs/
│   ├── getting-started.md     # START HERE — onboarding walkthrough for new contributors
│   ├── data-model.md          # Full data model specification (column dictionary)
│   ├── scoring-methodology.md # Execution-score formula, weights, worked examples, versioning
│   ├── data-lineage.md        # Provenance narrative: where every source/link/fix came from
│   └── project-roadmap.md     # Detailed product roadmap
├── db/
│   ├── schema.sql             # SQLite database schema
│   ├── load.py                # CSV → SQLite loader (fresh rebuild; FK + score gates; staleness guard)
│   ├── calculate_scores.py    # Execution-score calculator (--update-csv syncs projects.csv)
│   ├── update.py              # Incremental append-only mutator (add-event/source/evidence, set-status, relink, reverify)
│   ├── export_csv.py          # DB → CSV checkpointer (stamps db_meta watermark; recomputes scores)
│   ├── verify.py              # Article↔DB contract verifier (81 checks; exit non-zero on drift)
│   ├── verify_sources.py      # Source URL liveness checker (--apply stamps last_verified + url_status)
│   ├── verify_docs.py         # Doc-figure drift detector (scans docs/*.md + README for stale cited numbers)
│   ├── health.py              # One-command consistency gate (tests → load → verify → verify_sources → verify_docs); --fast for pre-commit
│   ├── changelog.py            # Read-only change_log audit digest (checkpoint status, score movers, new sources, invariants)
│   ├── query.py               # Read-only JSON query API (filters: sector/province/org/edition/status/score)
│   ├── investment_tracker.db  # The database (rebuilt from CSVs)
│   └── _extract/              # Source-extraction provenance (2026-07-25 re-linking)
├── tests/
│   └── test_calculate_score.py  # Formula unit tests (in-memory DB; run: python -m unittest discover tests)
├── data/
│   ├── projects.csv           # Project records (Phase 0: FILDA 2023)
│   ├── organizations.csv      # Organization records
│   ├── events.csv             # Event timeline records
│   ├── sources.csv            # Source/reference records (+ last_verified, url_status)
│   ├── project_evidence.csv   # Field-level provenance (project field → source)
│   ├── change_log.csv         # Append-only audit trail of every update.py mutation (+ load-seed/export-csv markers)
│   └── db_meta.csv            # Checkpoint watermark (last_exported_at) + score_version
├── articles/                  # Published analyses (Substack / EN / PT / LinkedIn) — pinned to DB figures by verify.py
├── landing-page/
│   └── index.html             # Demand validation landing page
└── research/
    └── filda-2023-raw.md       # Raw research notes for Phase 0
```

**New?** Read [`docs/getting-started.md`](docs/getting-started.md) — a 10-step
walkthrough from clone to your first round-trip change.