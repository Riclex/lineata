# Maintenance Cadence

The database's value is continuous verification. This is the cadence that keeps
the trust pillar intact. All commands run from the repo root.

## Every commit (automated)

The pre-commit hook (`scripts/install_hooks.py`) runs `python db/health.py
--fast` — unit tests + structural invariants. No rebuild, no network, so a code
commit is never blocked by an unrelated data-checkpoint lag.

Install once: `python scripts/install_hooks.py`.

## Before every publish (manual)

    python db/health.py            # full gate: tests -> load.py round-trip ->
                                  #   verify_invariants -> verify_snapshot ->
                                  #   verify_sources -> verify_docs

`--no-network` skips URL liveness if you are offline. Non-zero exit = do not
publish.

## After every incremental update session (manual)

    python db/update.py <cmd> --apply     # append mutations
    python db/export_csv.py --apply       # checkpoint CSVs + stamp watermark
    python db/verify_snapshot.py --update # regenerate db/snapshot.json, commit it
    python db/load.py                     # confirm the round-trip still rebuilds

`load.py` refuses to rebuild if the live DB has uncheckpointed `change_log`
mutations — run `export_csv.py --apply` first.

`export_csv.py --apply` writes a pre-checkpoint safety backup
(`db/investment_tracker.db.bak`) before touching the CSVs — the DB is
gitignored, so this is the only on-disk fallback between checkpoints if
something goes wrong mid-checkpoint. Only the most recent backup is kept.

## Source URL liveness (~every 30 days, or before publish)

    python db/verify_sources.py --apply   # re-classify all source URLs, stamp
                                          #   last_verified + url_status
    python db/update.py reverify --apply  # re-check stale/dead sources only

Dead/blocked URLs should be archived (`db/_extract/archive_sources.py`) or
re-grounded (`update.py relink --source-url`) rather than left to rot.

## When the data deliberately changes

After an intended data/formula change, regenerate the snapshot baseline and
commit it together with any updated articles:

    python db/verify_snapshot.py --update
    git add db/snapshot.json articles/ docs/scoring-methodology.md
    git commit -m "data: <change>"

`verify_snapshot.py` (without `--update`) catches *unintended* drift against the
committed baseline; `--update` records an *intended* change.

## Monthly digest (optional, for the waitlist)

    python db/digest.py --days 30         # print the status-change digest
    python db/digest.py --days 30 --out digest/2026-08.md
