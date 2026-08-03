# Execution Score Methodology

## Principle

Every project gets a score from 0 to 100 based on objective, reproducible criteria. No subjective judgment. Any researcher with the same data should arrive at the same score.

## The Formula

```
Execution Score = Base Score + Event Points + Evidence Bonus
                  - Delay Penalty - Status Penalty - Only-Announcement Penalty
```

Clamped to [0, 100]. Implemented verbatim in `db/calculate_scores.py`; the worked
examples below are generated from that script and reproduce exactly.

## Components

### 1. Base Score (from current status field)

| Status | Base Score |
|--------|-----------|
| completed | 70 |
| operational | 60 |
| restarted | 45 |
| under_construction | 40 |
| financed | 35 |
| mou_signed | 25 |
| announced | 15 |
| delayed | 10 |
| suspended | 5 |
| cancelled | 0 |
| unknown | 10 |

> **Unscored projects (`evidence_complete = 0`).** A project flagged `evidence_complete = 0` is *tracked but not scored*: it gets a score of **0** and is **excluded from published averages and distributions**. This operationalises the goal's rule — "don't score projects unless someone can click through the evidence." The entry stays in the database as an open record (it was announced, so it belongs in the historical record) but cannot move the headline number without a verifiable source. Currently: `banco-sol-mc-empresas-2025` (the supposed FILDA 2025 Cartão Multicaixa Empresas launch could not be grounded in any public source and appears misattributed). See `docs/data-lineage.md` § "Event 80 (Banco Sol)".

### 2. Event Points (from events table)

Each event recorded in the project's timeline contributes points. **v2 (adopted 2026-07-31): event points sum by distinct event type, not raw count** — see § Versioning. Duplicate coverage of the same milestone no longer stacks; rich, diverse timelines are still rewarded.

| Event Type | Points |
|-----------|--------|
| completion | +15 |
| expansion | +10 |
| groundbreaking | +8 |
| construction | +8 |
| financing | +7 |
| restart | +5 |
| mou | +5 |
| announcement | +3 |
| ownership_change | 0 |
| delay | 0 |
| suspension | 0 |
| closure | 0 |

Maximum event points: +30 (capped to prevent inflation)

### 3. Evidence Bonus (concrete proof of delivery)

Up to +10 additional points, computed by `calculate_evidence_bonus()` from
signals present in the project record and its events:

| Signal (as checked by the script) | Points (max +10 total) |
|-----------------------------------|------------------------|
| `estimated_jobs` > 0 (a verified job number on the project) | +3 |
| `actual_completion` recorded (a completion date on the project) | +3 |
| At least one `completion`, `construction`, or `groundbreaking` event | +2 |
| At least one `expansion` event (proxy for awards / capacity growth) | +2 |

**Note on source confidence (v2, adopted 2026-07-31):** evidence signals are
**scaled by the backing source's confidence** (high = 1.0, medium = 0.5,
low/NULL = 0.0). Jobs and actual_completion use the `project_evidence` source
confidence; production and expansion use the max confidence among the
category's event sources. Rounded half-up to an int. See § Versioning.

### 4. Delay Penalty

Based on years between announcement date and the latest event date (or current date if no completion):

| Delay | Penalty |
|-------|---------|
| 0-1 years | 0 |
| 1-2 years | -5 |
| 2-3 years | -10 |
| 3+ years | -15 |

### 5. Status Penalty

Applied by `STATUS_PENALTIES` based on the project's `status` field:

| Condition | Penalty |
|-----------|---------|
| Status is "delayed" | -10 |
| Status is "suspended" | -15 |
| Status is "unknown" (no post-announcement trace) | -10 |

### 6. Only-Announcement Penalty

A separate check (`calculate_only_announcement_penalty`): if the *only* event
type recorded for a project is `announcement` — i.e. no follow-up event of any
kind — an additional **-10** is applied. This catches announcements that
generated no traceable downstream activity.

## Worked Examples

These are the actual component breakdowns reported by
`python db/calculate_scores.py --verbose` against the current database — they
reproduce exactly.

### Huatong Angola Industry (`huatong-angola-industry-awards`)
- Base (operational): 60
- Events: +25 (v2 distinct types: construction +8, expansion +10, financing +7 = 25)
- Evidence: +8 (jobs +2, actual_completion +2, production +2, expansion +2 — confidence-weighted)
- Delay: -15 (3+ years, 2023→2026)
- Status penalty: 0 · Only-announcement: 0
- **Score: 60 + 25 + 8 - 15 = 78**

### Linha Verde (`linha-verde-investor-visas`)
- Base (delayed): 10
- Events: +3 (announcement only beyond the delay event)
- Evidence: 0
- Delay: 0
- Status penalty: -10 (delayed) · Only-announcement: 0
- **Score: 10 + 3 + 0 - 0 - 10 = 3**

### Investment Attraction Portal (`investment-portal-georeferenced`)
- Base (operational): 60
- Events: +18
- Evidence: +3 (actual_completion +2, production +1 — confidence-weighted)
- Delay: 0
- Status penalty: 0 · Only-announcement: 0
- **Score: 60 + 18 + 3 - 0 = 81**

## Scoring Script

Scores are calculated by `db/calculate_scores.py`, which reads from the SQLite
database and updates the `execution_score` column. This ensures consistency —
the same data always produces the same score.

The score stored in `data/projects.csv` is a **snapshot** that must stay in sync
with the formula. Two controls enforce this:

- `python db/load.py` recomputes scores after loading and **fails** if the CSV
  snapshot disagrees with the formula (score-consistency gate).
- `python db/calculate_scores.py --update-csv` recomputes and rewrites the
  `execution_score` column in `data/projects.csv` so the snapshot can be
  refreshed after any data edit.

## Limitations

1. The formula rewards having more events recorded. Projects with better press coverage may score higher. This is a known bias.
2. The "announced_value" field is not used in scoring. A $1B announcement and a $1M announcement are scored the same. This is intentional — the score measures execution, not scale.
3. Evidence bonus is inferred from structural signals (`estimated_jobs` > 0, a recorded `actual_completion`, and the presence of completion/construction/groundbreaking or expansion events) rather than from explicit evidence tags. As of v2-2026-07 it **does** weight by `sources.confidence` (high = full, medium = half, low/NULL = 0). Explicit evidence tagging (jobs_verified, exports_documented, etc.) would make this more rigorous.
4. The methodology will evolve. As we collect more data (especially post-2023 backfill), we may adjust weights. Stored scores now carry a **formula version stamp** (`SCORE_VERSION` in `calculate_scores.py`, persisted in `db_meta.score_version`) — see § Versioning below. When weights change, bump the version and re-snapshot so old scores stay reproducible.

5. **Recency / survivorship bias in the edition averages.** The mean score rises steadily by edition (2022: 51.4 → 2026: 80.0). Some of that is real — recent announcements still have active press coverage and operating status. But part is mechanical, and it is the same structural bias as limitation #1: older projects have had more time to stall *and* to lose their press trail, so they record fewer dated events and earn fewer event points. A project that executed well in 2022 but attracted no follow-up reporting will score lower than an otherwise-identical 2026 project simply because more of its timeline is recoverable today. Read the edition trend as a signal contaminated by research coverage, not as a clean measure of improving execution. The edition lens just makes the coverage bias visible; it does not create a new one.

## Versioning

Every stored `execution_score` is a snapshot produced by a specific set of weights. To keep historical scores reproducible after a weight change:

- `calculate_scores.py` exposes a `SCORE_VERSION` string (currently `v2-2026-07`) naming the weights defined above.
- `db/load.py` stamps it into `db_meta.score_version` on every rebuild (`INSERT OR IGNORE`, then warns if the loaded row disagrees with the current constant — a stale-snapshot signal).
- `db/verify_invariants.py` asserts the DB's `score_version` row exists and matches `calculate_scores.SCORE_VERSION`.
- The score-consistency gate in `load.py` is the real backstop: changing a weight without refreshing the `projects.csv` snapshot fails the rebuild, because the recomputed scores no longer match the stored ones.

**To change a weight:** (1) bump `SCORE_VERSION` and record the change here; (2) run `python db/calculate_scores.py --update-csv` to re-snapshot; (3) update `db/snapshot.json` via `python db/verify_snapshot.py --update` and the published article figures **together** (a weight change moves scores → counts/averages/distribution drift by design); (4) `python db/load.py && python db/verify_invariants.py && python db/verify_snapshot.py` to confirm. Do not change weights and articles in separate unverified steps.

### 2026-07-31 — v2-2026-07

1. **Event points score distinct types, not raw count.** Duplicate coverage of the same milestone no longer stacks. A project with three `announcement` events earns +3 (not +9). Rich, diverse timelines are still rewarded; repetitive coverage is not. See § Event Points.
2. **Evidence bonus weighted by source confidence.** Each signal is scaled by its backing source's confidence (high = 1.0, medium = 0.5, low/NULL = 0.0). Jobs/actual_completion use the `project_evidence` source; production/expansion use the max confidence among the category's event sources. Rounded half-up. See § Evidence Bonus.
3. **17 operational-without-progress projects downgraded to `announced`.** Projects with status `operational` but no progress event (completion/construction/groundbreaking/financing) were downgraded via `update.py set-status`, each with its own announcement source URL. 12 award-only + 5 announce-only projects moved from operational → announced, pulling the headline average from 61 → 44 and shifting the distribution from 10/1/7/20/12 → 16/12/2/13/7. The 5 "mixed" operational projects (award/expansion events that are not purely award-like) remain operational.

## v2 — coverage-adjusted event points (adopted 2026-07-31)

This change was adopted as part of `SCORE_VERSION = v2-2026-07`. It addresses limitation #1 (coverage bias) directly. The original proposal is preserved below for the record.

**Problem.** Event points are additive: `sum(EVENT_POINTS[e] for e in events)`, capped at 30. A project covered by six press mentions earns more than an identical project covered by two, regardless of execution. More sourcing sharpens this rather than fixing it — scores become more dependent on research effort, not less.

**Variant.** Score event points by *distinct event type* rather than raw event count, so duplicate coverage of the same milestone no longer stacks:

```python
def calculate_event_points_v2(conn, project_id):
    types = {e[0] for e in conn.execute(
        "SELECT event_type FROM events WHERE project_id = ?", (project_id,))}
    total = sum(EVENT_POINTS.get(t, 0) for t in types)   # each type counted once
    return min(total, MAX_EVENT_POINTS)
```

A project with three `announcement` events earns +3 (not +9). A project with `announcement` + `mou` + `construction` earns 3 + 5 + 8 = 16. Rich, diverse timelines are still rewarded; repetitive coverage is not.

**Predicted impact (to confirm before adopting).** Projects whose event timelines are dominated by repeated low-value mentions (typical of well-covered but slow projects) lose points; projects with a few distinct milestones are unchanged or gain relatively. The headline average and the 10/1/7/20/12 distribution will both shift. Before adopting: recompute under v2, list the movers, sanity-check that the moves are *coverage* corrections and not regressions for genuinely well-executed projects, then update the article + `verify.py` together under the versioning procedure above.

**What it does not fix.** It removes *within-project* duplication inflation. It does not remove *cross-project* coverage disparity (a project with one sourced milestone still beats a project with none). A coverage-adjustment factor (e.g. normalising by an estimate of available sourcing) would address that, but introduces a judgment input the current formula deliberately avoids. The v2 variant here is the conservative step; the coverage factor is a harder, separate decision.