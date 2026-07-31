#!/usr/bin/env python3
"""
Shared audit-trail writer for the FILDA Investment Execution Database pipeline.

`log_change` appends one row to the append-only `change_log` table. It is the
single implementation of the audit write so that db/update.py (the mutator) and
db/verify_sources.py (the liveness re-stamper) record mutations identically.

Extracting it here breaks what was otherwise a circular import:
db/update.py imports db/verify_sources.py (for `classify`), so verify_sources.py
cannot import back from update.py. Both import `log_change` from this module
instead.
"""

import json


def log_change(conn, operation, target_table, target_id, payload, source_url, note):
    """Append one change_log row inside the caller's open transaction.

    target_id is coerced to TEXT so a single column matches both TEXT and
    INTEGER primary keys (see verify.py's orphan-target checks, which cast the
    target column to TEXT for comparison).
    """
    conn.execute(
        "INSERT INTO change_log (operation, target_table, target_id, payload_json, "
        "source_url, note) VALUES (?, ?, ?, ?, ?, ?)",
        (operation, target_table,
         str(target_id) if target_id is not None else None,
         json.dumps(payload, ensure_ascii=False) if payload is not None else None,
         source_url, note))