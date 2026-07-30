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

Each event recorded in the project's timeline contributes points. Multiple events = stronger evidence of progress.

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

**Note on source confidence:** an earlier version of this section stated that
evidence points were weighted by `sources.confidence` (high = full, medium =
half, low = 0). That gating is **not implemented** in `calculate_scores.py` —
the evidence bonus currently ignores confidence. Treating evidence from a
low-confidence source the same as a high-confidence one is a known limitation;
see § Limitations.

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
- Events: +30 (capped — multiple timeline events)
- Evidence: +10 (jobs +3, actual_completion +3, a production event +2, and an `expansion` event — the Apr-2026 first 1,000-ton export — +2)
- Delay: -15 (3+ years, 2023→2026)
- Status penalty: 0 · Only-announcement: 0
- **Score: 60 + 30 + 10 - 15 = 85**

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
- Evidence: +5
- Delay: 0
- Status penalty: 0 · Only-announcement: 0
- **Score: 60 + 18 + 5 - 0 = 83**

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
3. Evidence bonus is inferred from structural signals (`estimated_jobs` > 0, a recorded `actual_completion`, and the presence of completion/construction/groundbreaking or expansion events) rather than from explicit evidence tags. It does **not** weight by `sources.confidence` despite that being a documented intent — see § Evidence Bonus. Explicit evidence tagging (jobs_verified, exports_documented, etc.) would make this more rigorous.
4. The methodology will evolve. As we collect more data (especially post-2023 backfill), we may adjust weights. Stored scores now carry a **formula version stamp** (`SCORE_VERSION` in `calculate_scores.py`, persisted in `db_meta.score_version`) — see § Versioning below. When weights change, bump the version and re-snapshot so old scores stay reproducible.

5. **Recency / survivorship bias in the edition averages.** The mean score rises steadily by edition (2022: 51.4 → 2026: 80.0). Some of that is real — recent announcements still have active press coverage and operating status. But part is mechanical, and it is the same structural bias as limitation #1: older projects have had more time to stall *and* to lose their press trail, so they record fewer dated events and earn fewer event points. A project that executed well in 2022 but attracted no follow-up reporting will score lower than an otherwise-identical 2026 project simply because more of its timeline is recoverable today. Read the edition trend as a signal contaminated by research coverage, not as a clean measure of improving execution. The edition lens just makes the coverage bias visible; it does not create a new one.

## Versioning

Every stored `execution_score` is a snapshot produced by a specific set of weights. To keep historical scores reproducible after a weight change:

- `calculate_scores.py` exposes a `SCORE_VERSION` string (currently `v1-2026-07`) naming the weights defined above.
- `db/load.py` stamps it into `db_meta.score_version` on every rebuild (`INSERT OR IGNORE`, then warns if the loaded row disagrees with the current constant — a stale-snapshot signal).
- `db/verify.py` asserts the DB's `score_version` row exists and matches `calculate_scores.SCORE_VERSION`.
- The score-consistency gate in `load.py` is the real backstop: changing a weight without refreshing the `projects.csv` snapshot fails the rebuild, because the recomputed scores no longer match the stored ones.

**To change a weight:** (1) bump `SCORE_VERSION` and record the change here; (2) run `python db/calculate_scores.py --update-csv` to re-snapshot; (3) update `verify.py` expectations and the published article figures **together** (a weight change moves scores → counts/averages/distribution drift by design); (4) `python db/load.py && python db/verify.py` to confirm. Do not change weights and articles in separate unverified steps.

## Proposed v2 — coverage-adjusted event points (draft, NOT applied)

This is a proposal to address limitation #1 (coverage bias) directly. It is **not applied** — adopting it is a versioned formula bump that moves scores and therefore requires the article + `verify.py` cascade above. It is written up here so the decision and its trade-offs are on the record.

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