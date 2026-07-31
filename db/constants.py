#!/usr/bin/env python3
"""
Shared operation vocabulary for the FILDA Investment Execution Database pipeline.

Single source of truth for the change_log operation vocabulary so the
staleness guard (db/load.py), the article<->DB contract (db/verify.py), and the
audit digest (db/changelog.py) can never drift out of sync again.

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
                 written by load.py / export_csv.py. Used by verify.py's
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
)

# MUTATION_OPS plus the checkpoint markers written by load.py / export_csv.py
# (these are NOT mutations -- they record rebuilds/checkpoints, not data edits).
ALLOWED_OPS = MUTATION_OPS + ("export-csv", "load-seed")


# Event descriptions that indicate a recognition/award, not a real project
# completion. Used by db/verify.py Check A to forbid award events typed as
# `completion` (which would inflate the score +15 and the completion-rate view).
# Validated against the 2026-07-31 dataset: catches all 18 award-completion
# events, 0 false positives on the 11 genuine completion events
# (conferences/delegations/launches). If a future completion description is
# ambiguous, assess it manually rather than over-fitting this list.
AWARD_INDICATORS = (
    "award", "awards", "prize", "prizes", "prémio", "premio",
    "aipex", "leao de ouro", "leão de ouro", "grand prize",
    "grand premio", "grand prémio", "bci challenge", "best participation",
)


def looks_like_award(text):
    """True if text reads as a recognition/award rather than a real completion."""
    if not text:
        return False
    t = text.lower()
    return any(k in t for k in AWARD_INDICATORS)