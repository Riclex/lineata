#!/usr/bin/env python3
"""
Shared vocabulary and utilities for the FILDA Investment Execution Database pipeline.

Single source of truth for the change_log operation vocabulary so the
staleness guard (db/load.py), the invariant checker (db/verify_invariants.py),
and the audit digest (db/changelog.py) can never drift out of sync again.

Background: the staleness guard in load.py originally enumerated the mutation
operations by name and omitted the `relink-event` / `relink-evidence` ops that
db/update.py writes. The guard therefore under-counted uncheckpointed
mutations, and a rebuild after an uncheckpointed relink would have SILENTLY
LOST the relink. Single-sourcing the set here closes that class of bug: every
mutator op db/update.py writes is in MUTATION_OPS, and every consumer imports
the same tuple.

  MUTATION_OPS : operations that represent real data mutations written by
                 db/update.py. Used by the staleness guard (load.py refuses to
                 rebuild if any of these are newer than last_exported_at) and by
                 changelog.py's uncheckpointed-mutation count.
  ALLOWED_OPS  : every operation that may legally appear in change_log -- the
                 mutation set plus the load-seed / export-csv checkpoint markers
                 written by load.py / export_csv.py. Used by verify_invariants.py's
                 "change_log operations all in allowed set" check.

Keep MUTATION_OPS in sync with COMMANDS in db/update.py (every command that
writes a change_log mutation row must appear here).
"""

# db/update.py mutators. Order is stable for deterministic placeholder expansion.
MUTATION_OPS = (
    "add-source",
    "add-event",
    "add-evidence",
    "set-status",
    "relink-event",
    "relink-evidence",
    "reverify",
    "retype-event",
    "set-blocked",
)

# MUTATION_OPS plus the checkpoint markers written by load.py / export_csv.py
# (these are NOT mutations -- they record rebuilds/checkpoints, not data edits).
ALLOWED_OPS = MUTATION_OPS + ("export-csv", "load-seed")


# Event descriptions that indicate a recognition/award, not a real project
# completion. Used by db/verify_invariants.py Check A to forbid award events typed
# as `completion` (which would inflate the score +15 and the completion-rate view).
# Validated against the 2026-07-31 dataset: catches all 18 award-completion
# events, 0 false positives on the 11 genuine completion events
# (conferences/delegations/launches). If a future completion description is
# ambiguous, assess it manually rather than over-fitting this list.
AWARD_INDICATORS = (
    "award", "awards", "prize", "prizes", "prémio", "premio",
    "aipex", "leao de ouro", "leão de ouro", "grand prize",
    "grand premio", "grand prémio", "bci challenge", "best participation",
)

# Score distribution bucket boundaries. Single-sourced here so the score
# report (db/calculate_scores.py), the query summary (db/query.py), and the
# snapshot verifier (db/verify_snapshot.py) can never drift out of sync.
# Keys are the published bucket labels; values are the inclusive upper bound.
SCORE_BUCKETS = (
    ("0-20", 20),
    ("21-40", 40),
    ("41-60", 60),
    ("61-80", 80),
    ("81-100", 100),
)


def score_distribution(scores):
    """Bucket a list of scores into the published distribution.

    Returns an ordered dict {bucket_label: count} matching SCORE_BUCKETS order.
    Used by the score report, the query summary, and the snapshot verifier so
    all three derive the distribution from one definition.
    """
    dist = {label: 0 for label, _ in SCORE_BUCKETS}
    for s in scores:
        for label, upper in SCORE_BUCKETS:
            if s <= upper:
                dist[label] += 1
                break
    return dist


def looks_like_award(text):
    """True if text reads as a recognition/award rather than a real completion."""
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in AWARD_INDICATORS)


# Execution band — the coarse public-facing label (guideline §0.A halfway
# version). The 0-100 execution_score stays as the analytical detail; this enum
# is the primary published category, derived mechanically from the existing
# fields (no judgment input, not stored). Hybrid derivation (the project's
# choice): status sets the band, the score refines the upper bands.
#   UNCONFIRMED : evidence_complete = 0 (tracked but unscored, e.g. Banco Sol)
#   STALLED     : status delayed/suspended
#   DELIVERED   : status completed, OR score >= 81 (strong verifiable execution)
#   IN_PROGRESS : status operational/under_construction/restarted, OR score 41-80
#   SILENT      : everything else (announced/unknown/etc. with score < 41 -- the
#                 "thin public trail" cases; low score == thin record, not failure)
EXECUTION_BANDS = ("UNCONFIRMED", "STALLED", "DELIVERED", "IN_PROGRESS", "SILENT")


# Source program — which announcement channel a project entered the database
# through. The database started FILDA-only (51 projects, 2022-2026); Tier 3
# coverage expansion broadens it to AIPEX-promoted, PPP, multilateral-funded,
# and standalone refinery builds. Single-sourced here so verify_invariants.py
# can enforce the allowed set (the projects column is plain TEXT with a DEFAULT
# 'FILDA'; projects are added via CSV, not update.py, so the invariant checker
# is the backstop, not the schema).
SOURCE_PROGRAMS = ("FILDA", "AIPEX", "refinery", "PPP", "multilateral")


# project_evidence.field controlled vocabulary. The four in-use fields back
# project fields directly (status, announced_value, actual_completion,
# estimated_jobs); the four outcome tags let a human record a verified outcome
# (jobs created, production started, exports documented, awards won) with a
# source, instead of inferring it from event types. Enforced by
# verify_invariants.py (the column is plain TEXT; the invariant checker is the
# backstop, same as SOURCE_PROGRAMS). Wiring the outcome tags into the evidence
# bonus is a deliberate future v3 scoring change — see data-lineage.md
# recommendation #9 and scoring issue #4.
#
# ADDABLE_EVIDENCE_FIELDS is the subset db/update.py add-evidence accepts today
# (the four fields that back project columns directly); the outcome tags are
# deliberately NOT CLI-addable until the v3 evidence-bonus wiring lands.
# update.py imports this set rather than duplicating a literal, so the CLI
# vocabulary can never drift from the invariant vocabulary.
ADDABLE_EVIDENCE_FIELDS = (
    "status", "announced_value", "actual_completion", "estimated_jobs",
)
EVIDENCE_FIELDS = ADDABLE_EVIDENCE_FIELDS + (
    "jobs_verified", "production_started", "exports_documented", "awards_won",
)


def execution_band(status, score, evidence_complete):
    """Derive the coarse execution-band label from existing project fields.

    Pure function of (status, execution_score, evidence_complete) -- no
    judgment, no storage. Status sets the band; the score refines DELIVERED vs
    IN_PROGRESS (and can promote a thin-trail project up). See EXECUTION_BANDS
    above for the exact mapping.
    """
    if not evidence_complete:
        return "UNCONFIRMED"
    if status in ("delayed", "suspended"):
        return "STALLED"
    if status == "completed" or score >= 81:
        return "DELIVERED"
    if status in ("operational", "under_construction", "restarted") or 41 <= score <= 80:
        return "IN_PROGRESS"
    return "SILENT"


def band_distribution(rows):
    """Count rows into the EXECUTION_BANDS buckets.

    `rows` is an iterable of (status, score, evidence_complete) tuples. Returns
    an ordered dict {band: count} matching EXECUTION_BANDS order. Used by the
    query summary so the by_band view derives from one definition.
    """
    dist = {b: 0 for b in EXECUTION_BANDS}
    for status, score, evidence_complete in rows:
        dist[execution_band(status, score, evidence_complete)] += 1
    return dist


# data_completeness — how much of a project's timeline is recorded. Derived
# mechanically from the event-type set (no judgment, no storage of the
# derivation): announcement_only = no progress events, partial = progress but
# no completion, full = a completion event. Single-sourced here so load.py's
# consistency gate and verify_invariants.py's drift check share one definition.
DATA_COMPLETENESS = ("announcement_only", "partial", "full")

# Event types that move a project past the announcement-only stage.
PROGRESS_EVENTS = ("financing", "groundbreaking", "construction", "expansion", "restart")


def data_completeness(event_types):
    """Derive the timeline-completeness label from a project's event types.

    Pure function of the event-type set:
      announcement_only : no progress events (only announcement/mou/delay/...)
      partial           : has progress events but no completion
      full              : has a completion event
    """
    types = set(event_types)
    if "completion" in types:
        return "full"
    if types & set(PROGRESS_EVENTS):
        return "partial"
    return "announcement_only"