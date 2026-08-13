#!/usr/bin/env python3
"""
Monthly status-change digest for the Angola Investment Execution Database.

The workflow-integration slice: a "decision delivered where users work." Reads
the change_log audit trail for the last N days (default 30), groups by project,
and emits a markdown digest of status changes, score movers, new evidence, and
new sources — each with its source URL and date. Email-ready text; does not send.

Read-only (immutable mode).

Usage:
    python db/digest.py                      # last 30 days, to stdout
    python db/digest.py --days 60            # last 60 days
    python db/digest.py --since 2026-07-01   # since a date
    python db/digest.py --days 30 --out digest/2026-08.md
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_dir = os.path.dirname(os.path.abspath(__file__))
DB_path = os.path.join(DB_dir, "investment_tracker.db")
BASE_dir = os.path.dirname(DB_dir)

sys.path.insert(0, DB_dir)
from constants import MUTATION_OPS  # noqa: E402  (after sys.path is set)

STATUS_OPS = ("set-status",)
SCORE_OPS = ("add-event", "retype-event")
EVIDENCE_OPS = ("add-evidence",)
SOURCE_OPS = ("add-source",)
# L4: ops the dispatch previously dropped silently. Each now lands in a per-
# project "other" bucket so the audit trail is complete.
BLOCKED_OPS = ("set-blocked",)
RELINK_OPS = ("relink-event", "relink-evidence")
REVERIFY_OPS = ("reverify",)


def _payload(row):
    try:
        return json.loads(row["payload_json"]) if row["payload_json"] else {}
    except json.JSONDecodeError:
        return {}


def _project_title(conn, pid):
    if not pid:
        return None
    r = conn.execute("SELECT title FROM projects WHERE id=?", (pid,)).fetchone()
    return r[0] if r else pid


def gather(conn, since_iso):
    """Collect change_log rows since `since_iso`, grouped by project.

    Returns (by_project, warnings). `by_project` maps pid -> a dict of buckets
    (status/scores/evidence/sources/other). `warnings` lists any op that IS in
    constants.MUTATION_OPS but has no bucket here — a future op added to the
    vocabulary without a digest bucket is surfaced instead of silently dropped
    (L4). Checkpoint markers (export-csv/load-seed) and anything outside
    MUTATION_OPS are ignored."""
    by_project = {}  # pid -> {"title":.., "status":[],"scores":[],"evidence":[],"sources":[],"other":[]}
    warnings = []

    def bucket(pid):
        pid = pid or "(no project)"
        if pid not in by_project:
            by_project[pid] = {"title": _project_title(conn, pid),
                               "status": [], "scores": [], "evidence": [],
                               "sources": [], "other": []}
        return by_project[pid]

    rows = conn.execute(
        "SELECT ts, operation, target_table, target_id, payload_json, source_url, note "
        "FROM change_log WHERE ts >= ? ORDER BY ts", (since_iso,)).fetchall()

    for r in rows:
        op = r["operation"]
        p = _payload(r)
        if op in STATUS_OPS:
            pid = p.get("project_id")
            bucket(pid)["status"].append(
                {"ts": r["ts"], "old": p.get("status_old"), "new": p.get("status_new"),
                 "source_url": r["source_url"], "note": r["note"]})
        elif op in SCORE_OPS:
            pid = p.get("project_id")
            if "score_old" in p and "score_new" in p and p["score_old"] != p["score_new"]:
                bucket(pid)["scores"].append(
                    {"ts": r["ts"], "old": p["score_old"], "new": p["score_new"],
                     "op": op, "source_url": r["source_url"]})
        elif op in EVIDENCE_OPS:
            pid = p.get("project_id")
            bucket(pid)["evidence"].append(
                {"ts": r["ts"], "field": p.get("field"), "value": p.get("value"),
                 "source_url": r["source_url"]})
        elif op in SOURCE_OPS:
            bucket("(new source)")["sources"].append(
                {"ts": r["ts"], "url": p.get("url"), "title": p.get("title"),
                 "publisher": p.get("publisher")})
        elif op in BLOCKED_OPS or op in RELINK_OPS or op in REVERIFY_OPS:
            # L4: previously dropped. Surface in a per-project "other" bucket.
            # Checked against the live component tuples (not a precomputed
            # OTHER_OPS) so a future op losing its bucket falls through to the
            # MUTATION_OPS warning below instead of being silently captured.
            pid = p.get("project_id")
            bucket(pid)["other"].append(
                {"ts": r["ts"], "op": op, "source_url": r["source_url"],
                 "note": r["note"], "payload": p})
        elif op in MUTATION_OPS:
            # A mutation op with no bucket here: a future op was added to the
            # vocabulary but digest.py wasn't updated. Warn loud instead of
            # dropping it silently (L4).
            warnings.append(
                f"unbucketed mutation op {op!r} in change_log (ts={r['ts']}) — "
                f"add a digest bucket or it is absent from the digest")
    return by_project, warnings


def render(by_project, since_iso, until_iso, warnings=None):
    lines = []
    lines.append(f"# FILDA Execution Digest — {until_iso}")
    lines.append(f"_Changes since {since_iso}._")
    lines.append("")
    n = sum(1 for b in by_project.values()
            if b["status"] or b["scores"] or b["evidence"] or b["other"])
    n_src = len(by_project.get("(new source)", {}).get("sources", []))
    lines.append(f"**{n}** project(s) with activity · **{n_src}** new source(s).")
    lines.append("")
    if warnings:
        lines.append("> ⚠️ **Digest warnings** — unbucketed mutation ops (add a "
                     "digest bucket in `db/digest.py`):")
        for w in warnings:
            lines.append(f"> - {w}")
        lines.append("")
    for pid, b in sorted(by_project.items()):
        if not (b["status"] or b["scores"] or b["evidence"] or b["other"]) and pid == "(new source)":
            if not b["sources"]:
                continue
        title = b["title"] or pid
        lines.append(f"## {title} (`{pid}`)")
        if b["status"]:
            lines.append("### Status changes")
            for e in b["status"]:
                lines.append(f"- `{e['ts']}` {e['old']} → **{e['new']}** "
                             f"— {e['source_url'] or '_(no source)'}"
                             + (f" — {e['note']}" if e['note'] else ""))
            lines.append("")
        if b["scores"]:
            lines.append("### Score movers")
            for e in b["scores"]:
                lines.append(f"- `{e['ts']}` **{e['old']} → {e['new']}** ({e['op']})"
                             + (f" — {e['source_url']}" if e['source_url'] else ""))
            lines.append("")
        if b["evidence"]:
            lines.append("### New evidence")
            for e in b["evidence"]:
                lines.append(f"- `{e['ts']}` {e['field']} = {e['value']!r}"
                             + (f" — {e['source_url']}" if e['source_url'] else ""))
            lines.append("")
        if b["other"]:
            lines.append("### Other audit entries")
            for e in b["other"]:
                lines.append(f"- `{e['ts']}` **{e['op']}**"
                             + (f" — {e['source_url']}" if e['source_url'] else "")
                             + (f" — {e['note']}" if e['note'] else ""))
            lines.append("")
        if pid == "(new source)" and b["sources"]:
            lines.append("### New sources")
            for e in b["sources"]:
                lines.append(f"- `{e['ts']}` {e['title'] or ''} ({e['publisher'] or ''})"
                             f" — {e['url']}")
            lines.append("")
    if not any(b["status"] or b["scores"] or b["evidence"] or b["other"] or b["sources"]
               for b in by_project.values()):
        lines.append("_No changes in this period._")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Monthly status-change digest.")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--since", help="ISO date YYYY-MM-DD (overrides --days)")
    ap.add_argument("--out", help="write to a file instead of stdout")
    args = ap.parse_args()

    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")
    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    until_iso = date.today().isoformat()
    since_iso = args.since if args.since else (date.today() - timedelta(days=args.days)).isoformat()
    by_project, warnings = gather(conn, since_iso)
    body = render(by_project, since_iso, until_iso, warnings=warnings)
    conn.close()

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(body)
        print(f"[OK] wrote {args.out}")
    else:
        sys.stdout.write(body)


if __name__ == "__main__":
    main()
