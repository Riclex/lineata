# Getting Started — for a new contributor

This is the "start here" path. The codebase has a lot of documentation; this
walks you through it in the order that makes the system click, then has you
make one safe, reversible change end to end. Aim: ~one afternoon to onboard
confidently.

You need only Python 3 (stdlib only — no pip install) and the files in this
repo. All commands run from the repo root.

## 0. The mental model (read, don't run)

Three things to hold in your head before touching anything:

1. **The asset is the database, not the articles.** Read
   `InvestmentExecutionDatabase-goal.md` — it's the product vision and the
   "why" behind every design decision. The articles are a distribution channel
   for the database.
2. **Three operating modes, not one.** Maintenance is *event-driven*, not a
   full rebuild. Read the README "Operating Modes" section:
   - **Seed** (rare): `db/load.py` — rebuild the DB from `data/*.csv` checkpoints.
   - **Append** (day-to-day): `db/update.py <cmd> --apply` — one mutation, one
     `change_log` row, one score recompute. No CSV editing.
   - **Checkpoint** (after a session): `db/export_csv.py --apply` — refresh the
     CSVs so `load.py` can reproduce the DB; stamps the watermark.
   - **The discipline:** always `export_csv.py --apply` after `update.py --apply`
     before rebuilding, or the staleness guard refuses the rebuild.
3. **No fabrication, ever.** Every `update.py` mutation requires a real,
     verifiable `--source-url`. If no grounded source exists, leave the link
     NULL rather than invent one. Banco Sol (event 80, `source_id IS NULL`,
     `evidence_complete = 0`) is the standing example.

## 1. Understand what the score is

Read `docs/scoring-methodology.md` end to end. It is the authoritative spec:
the formula, every weight table, worked examples that reproduce against the
script, the limitations, and the versioning procedure. The score is a
reproducible number, not an opinion — `db/calculate_scores.py` implements it
verbatim and `db/load.py` re-asserts it on every rebuild.

Key invariance to remember: **`source_id` is not an input to the formula.**
Re-linking a project's events to better sources never moves its score. Only
event *dates*, event *types*, and project *fields* do. This is the backbone of
the whole system and the reason the append-only audit layer is safe.

## 2. Understand the data shape

Read `docs/data-model.md` — the column dictionary for every table, the enums,
and the cross-references to the goal. Then skim `db/schema.sql` (it has inline
comments tying each table back to a goal principle).

The tables in one breath: `projects` (the things), `organizations` +
`project_organizations` (who's involved, with roles), `events` (the dated
timeline), `sources` (the articles), `project_evidence` (field-level
provenance — backs a project's own fields, not just its events),
`change_log` (append-only audit), `db_meta` (the checkpoint watermark +
`score_version`).

## 3. Build the database

```
python db/load.py
```

This rebuilds `db/investment_tracker.db` from `data/*.csv`, then runs two
integrity gates: a foreign-key check and a **score-consistency check** that
recomputes every score from the loaded data and asserts it matches the
`execution_score` snapshot in `projects.csv`. If it prints
`score consistency: OK`, the DB is in a reproducible state. If it fails, the
CSV snapshot is stale — run `python db/calculate_scores.py --update-csv` and
re-load.

## 4. Verify the published figures still match the DB

```
python db/verify.py
```

`verify.py` is the article↔DB contract: it pins every figure published in the
articles (counts, the 62.4 average, the distribution, all 13 sector averages,
private/gov averages, the seven case-study scores) to concrete DB queries and
exits non-zero on any drift. It currently runs 81 checks. **Any data edit that
moves a score must update `verify.py`'s expectations AND the article figures
together** — that's the cascade. A green `verify.py` means the published
analysis and the database agree.

## 5. See the score breakdown

```
python db/calculate_scores.py --verbose
```

This recomputes every score and prints the per-component breakdown
(base / events / evidence / delay / status_penalty / only_announce). Find
Huatong — you should see `base=60 events=+30 evidence=+10 delay=-15 ... = 85`.
This is the fastest way to understand *why* a project scores what it does.

## 6. Query the data (read-only)

```
python db/query.py --sector Energy
python db/query.py --province Bengo --min-score 60
python db/query.py --project chicomba-water-dam
python db/query.py --summary
```

`query.py` is the embryo of the product's API — read-only, immutable mode, JSON
out. By default it returns only scored projects (`evidence_complete = 1`),
matching the published figures. This is the "workflow integration" leg.

## 7. Make a dry-run change (no DB written)

```
python db/update.py add-event --project huatong-angola-industry-awards \
  --type expansion --date 2026-04-10 \
  --source-url "https://www.opais.ao/economia/bengo-inicia-hoje-primeira-exportacao-de-aluminio-produzido-no-parque-industrial-huatong-angola/" \
  --title "First 1,000 tons exported to the Netherlands"
```

Without `--apply`, `update.py` is a dry run: it prints the planned mutation and
the `score old -> new` line, then writes nothing. (That exact event already
exists, so you'll see a no-op or the current state — try changing the `--date`
to see a real planned insert without persisting.) This is how you preview any
change safely.

## 8. The full round-trip (the discipline in practice)

The reproducibility guarantee is: *any live-DB edit must survive a full CSV
rebuild.* The sequence, after a real `--apply`:

```
python db/update.py <cmd> ... --apply   # 1. mutate the live DB (one change_log row)
python db/export_csv.py --apply         # 2. checkpoint: refresh data/*.csv + stamp watermark
python db/load.py                       # 3. rebuild from CSV (FK + score gates)
python db/verify.py                     # 4. article figures still match?
```

If `verify.py` fails after a score-moving edit, it's because the published
figures need updating — that's the cascade, and it's *by design*. Update the
articles + `verify.py` expectations **together**, then re-run `load.py` +
`verify.py`. See `docs/data-lineage.md` § "Huatong April export event added
2026-07-30" for a worked example of the whole cascade.

## 9. Run the tests

```
python -m unittest discover tests
```

`tests/test_calculate_score.py` pins the formula itself (events cap, each
evidence signal, delay tiers, status penalties, clamps, the version stamp)
against `calculate_score` running on an in-memory DB. They're the fastest way to
see expected behavior and the safety net for any scoring-code change.

## 10. Where things live (read these next)

- `docs/data-lineage.md` — the provenance narrative: where every source came
  from, every re-linking decision, the Banco Sol exemption, the incremental
  layer. Read this when a number in the DB surprises you — the "why" is here.
- `db/_extract/README.md` — the distinction between *day-to-day* (`update.py`)
  and *bulk backfill* (the `_extract/` scripts). Read before touching `_extract/`.
- `db/verify_sources.py` — source URL liveness checker; stamps `url_status`
  (`alive` / `blocked` / `dead`).

## 11. Monitoring & audit

Three read-only scripts watch the codebase for drift. Run them before a publish
or when something feels off:

```
python db/health.py             # full gate: tests → load → verify → URL liveness → doc drift
python db/health.py --fast       # pre-commit: tests + verify.py only (~0.3s, no rebuild/network)
python db/verify_docs.py          # docs/*.md + README cited numbers still match the DB?
python db/changelog.py            # audit digest: checkpoint status, score movers, new sources
```

`health.py` is the one-command gate — it chains the whole integrity chain and
exits non-zero on the first failure, so it can gate a publish or a git
pre-commit hook (`--fast`). `verify_docs.py` is what catches a worked example
that goes stale after a score moves (the Huatong 83→85 case is why it exists).
`changelog.py` reads the `change_log` audit trail back to you — the checkpoint
status, which scores moved, what sources were added, and whether the
"only Banco Sol is unsourced" invariant still holds.

## The one-sentence version

> Read the goal → read the scoring methodology → `load.py` → `verify.py` →
> dry-run an `add-event` → `export_csv --apply` → `load.py` → `verify.py`. Never
> fabricate a URL; never edit a CSV by hand; always checkpoint after an append.

## Golden rules

- **No fabrication.** Real `--source-url` or `--clear` to NULL. Never invent.
- **Flag, don't silently fix.** Surface date/factual discrepancies; don't
  auto-correct pre-existing ones.
- **Append, don't overwrite.** The DB is a historical record; `change_log` is
  the audit trail.
- **Always checkpoint after an append** (`export_csv.py --apply`) before
  rebuilding, or the staleness guard refuses it.
- **A score change is a cascade.** Move a scored project's score → update the
  article figures + `verify.py` expectations together, then re-verify.