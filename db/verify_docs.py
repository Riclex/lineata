#!/usr/bin/env python3
"""
Doc-figure drift detector for the Angola Investment Execution Database.

db/verify_invariants.py pins the *structural* contract and db/verify_snapshot.py
pins the *article* figures to the DB snapshot, but the *docs* and README cite
the same kind of numbers by hand — and that is how a worked example goes stale
(e.g. scoring-methodology.md's Huatong example read 83 for a week after the
score moved to 85). This script scans docs/*.md + README.md for cited numbers
and compares them to the DB (and, for the contract check count, to the
verify_invariants.py + verify_snapshot.py reported summaries). It is a best-effort
regex sweep, not a full contract — a FAIL means a doc cites a number the DB
contradicts.

Read-only; runs db/verify_invariants.py and db/verify_snapshot.py as subprocesses
to get the authoritative check count (it does not parse their source, which is
loop-driven).

Usage:
    python db/verify_docs.py
"""

import os
import re
import subprocess
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
DOCS = os.path.join(BASE_dir, "docs")
README = os.path.join(BASE_dir, "README.md")
EXTRACT_README = os.path.join(BASE_dir, "db", "_extract", "README.md")
SCORING = os.path.join(DOCS, "scoring-methodology.md")
LINEAGE = os.path.join(DOCS, "data-lineage.md")
PY = sys.executable


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def first_int(text, pattern):
    """First integer captured by `pattern` in text, or None."""
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def first_float(text, pattern):
    """First float captured by `pattern` in text, or None."""
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def verify_check_count():
    """Run db/verify_invariants.py + db/verify_snapshot.py and sum their
    'NN checks' / passed summary lines into one total check count."""
    total = 0
    for script in ("verify_invariants.py", "verify_snapshot.py"):
        r = subprocess.run([PY, os.path.join("db", script)], cwd=BASE_dir,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        # verify_invariants prints "NN checks, ..."; verify_snapshot prints
        # "[OK] ..." (no check count) — count its drifts as 0 on success.
        m = re.search(r"(\d+) checks,\s+\d+ passed", r.stdout + r.stderr)
        if m:
            total += int(m.group(1))
    return total if total else None


def main():
    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")

    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # Authoritative DB values.
    n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    n_linked = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NOT NULL").fetchone()[0]
    avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects WHERE evidence_complete = 1"
    ).fetchone()[0]
    avg_rounded = round(avg, 4)

    vchk = verify_check_count()

    checks = []  # (label, ok, expected, actual, location)

    def chk(label, expected, actual, location, ok=None):
        if expected is None:
            checks.append((label, False, expected, actual, location))
            return
        ok = (expected == actual) if ok is None else ok
        checks.append((label, ok, expected, actual, location))

    # --- verify.py check count cited in README + _extract/README ---
    readme = read(README)
    chk("README 'verify.py (NN checks)'", vchk,
        first_int(readme, r"verify\.py[^\n]*\((\d+) checks"), "README.md")
    chk("README directory-tree 'contract verifier (NN checks)'", vchk,
        first_int(readme, r"contract verifier \((\d+) checks"), "README.md")
    if os.path.exists(EXTRACT_README):
        er = read(EXTRACT_README)
        chk("_extract/README 'NN article↔DB contract checks'", vchk,
            first_int(er, r"# (\d+) article"), "db/_extract/README.md")

    # --- data-lineage.md cited counts ---
    lineage = read(LINEAGE)
    chk("data-lineage '### NNN sources in the database'", n_sources,
        first_int(lineage, r"### (\d+) sources in the database"), "docs/data-lineage.md")
    chk("data-lineage counts table '| events | N |'", n_events,
        first_int(lineage, r"\| events \| (\d+) \|"), "docs/data-lineage.md")
    chk("data-lineage counts table '| sources | N |'", n_sources,
        first_int(lineage, r"\| sources \| (\d+) \|"), "docs/data-lineage.md")
    chk("data-lineage 'NN linked / 1 NULL'", n_linked,
        first_int(lineage, r"(\d+) linked / 1 NULL"), "docs/data-lineage.md")
    lin_avg = first_float(lineage, r"avg (\d+\.\d+) over the 50 scored")
    # Allow the lineage avg to be one- or two-decimal; compare with tolerance.
    if lin_avg is not None:
        chk("data-lineage verify.py desc 'avg N.NN over the 50 scored'",
            True, True, "docs/data-lineage.md",
            ok=abs(lin_avg - avg_rounded) <= 0.05)

    # --- scoring-methodology.md worked examples ---
    # Each "### Title (`project-id`)" heading is followed by a
    # "**Score: ... = NN**" line. Compare NN to the DB execution_score for that id.
    scoring = read(SCORING)
    lines = scoring.splitlines()
    i = 0
    while i < len(lines):
        hm = re.match(r"### .+\(`([\w-]+)`\)", lines[i])
        if hm:
            pid = hm.group(1)
            # Find the next "**Score: ... = NN**" within the next ~12 lines.
            for j in range(i + 1, min(i + 13, len(lines))):
                sm = re.search(r"Score:.*?=\s*(\d+)\*\*", lines[j])
                if sm:
                    db_score = conn.execute(
                        "SELECT execution_score FROM projects WHERE id = ?",
                        (pid,)).fetchone()
                    expected = db_score[0] if db_score else None
                    chk(f"scoring-methodology worked example '{pid}'",
                        expected, int(sm.group(1)),
                        f"docs/scoring-methodology.md:{j+1}")
                    i = j
                    break
        i += 1

    conn.close()

    # --- Report ---
    width = 56
    print(f"{'Check':<{width}}{'Result':>8}")
    print("-" * (width + 8))
    failures = 0
    for label, ok, expected, actual, location in checks:
        print(f"{label[:width]:<{width}}{'PASS' if ok else 'FAIL':>8}")
        if not ok:
            failures += 1
            print(f"    {location}: expected {expected!r}, doc says {actual!r}")
    print("-" * (width + 8))
    print(f"{len(checks)} checks, {len(checks) - failures} passed, {failures} failed")
    if failures:
        print("\n[FAIL] A doc cites a number the DB contradicts — update the doc, or fix the data.")
        sys.exit(1)
    print("\n[OK] Cited doc figures match the DB.")


if __name__ == "__main__":
    main()