#!/usr/bin/env python3
"""
One-command consistency gate for the Angola Investment Execution Database.

Runs the integrity chain in order and exits non-zero on the first failure, so
it can gate a publish, a scheduled run, or a git pre-commit hook:

    tests  →  load.py (round-trip rebuild)  →  verify_invariants.py  →  verify_snapshot.py  →  export_app_json.py --check  →  verify_sources.py  →  verify_docs.py

The --fast mode (for the pre-commit hook) runs only tests + verify_invariants.py: no
rebuild, no network, no doc sweep — a code edit shouldn't be blocked by a
data-checkpoint lag, a transient network failure, or a stale doc figure.

Each stage is a subprocess; a failing stage prints the tail of its stderr so
the cause is visible without a re-run.

Modes:
    python db/health.py            # full gate (default): tests + load + invariants + snapshot + URL liveness + docs
    python db/health.py --fast      # tests + verify_invariants.py only — no rebuild, no network.
                                    # Use this in the pre-commit hook so a code edit
                                    # isn't blocked by an unrelated data-checkpoint lag
                                    # or a transient network failure.
    python db/health.py --no-network  # tests + load + invariants + snapshot, skip URL liveness

Why --fast for the hook: load.py's round-trip rebuild refuses if the live DB
has uncheckpointed change_log mutations (the staleness guard). That is the
*correct* signal before a data publish, but a false positive for a *code* commit
(the DB checkpoint state has nothing to do with an edit to calculate_scores.py).
The full gate is the pre-publish check; --fast is the every-commit check.
"""

import argparse
import os
import subprocess
import sys
import time

# Force UTF-8 on stdout so the ↔/—/· characters in labels and captured
# subprocess output survive a Windows cp1252 console without crashing.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_dir = os.path.join(BASE_dir, "db")
PY = sys.executable


def run_stage(label, cmd, cwd=BASE_dir):
    """Run one stage; return (ok, elapsed_s, tail). tail = last ~8 lines of stderr+stdout on failure."""
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - t0
    if r.returncode == 0:
        return True, elapsed, ""
    tail = (r.stdout + r.stderr).strip().splitlines()[-8:]
    return False, elapsed, "\n".join(f"      | {ln}" for ln in tail)


def main():
    parser = argparse.ArgumentParser(description="Run the full consistency gate.")
    parser.add_argument("--fast", action="store_true",
                        help="tests + verify_invariants.py only (no rebuild, no network) — for the pre-commit hook")
    parser.add_argument("--no-network", action="store_true",
                        help="skip verify_sources.py URL liveness")
    args = parser.parse_args()

    if args.fast:
        stages = [
            ("unit tests", [PY, "-m", "unittest", "discover", "tests"]),
            ("structural invariants (verify_invariants.py)",
             [PY, os.path.join("db", "verify_invariants.py")]),
        ]
    else:
        stages = [
            ("unit tests", [PY, "-m", "unittest", "discover", "tests"]),
            ("round-trip rebuild (load.py)", [PY, os.path.join("db", "load.py")]),
            ("structural invariants (verify_invariants.py)",
             [PY, os.path.join("db", "verify_invariants.py")]),
            ("snapshot + article pin (verify_snapshot.py)",
             [PY, os.path.join("db", "verify_snapshot.py")]),
            ("static app JSON sync (export_app_json.py --check)",
             [PY, os.path.join("db", "export_app_json.py"), "--check"]),
        ]
        if not args.no_network:
            stages.append(
                ("source URL liveness (verify_sources.py --limit 20)",
                 [PY, os.path.join("db", "verify_sources.py"), "--limit", "20"]))
        stages.append(
            ("doc-figure drift (verify_docs.py)",
             [PY, os.path.join("db", "verify_docs.py")]))

    print("Angola Investment Execution Database — consistency gate")
    print("=" * 64)
    all_ok = True
    for label, cmd in stages:
        print(f"  {label:<46} ... ", end="", flush=True)
        ok, elapsed, tail = run_stage(label, cmd)
        if ok:
            print(f"PASS  ({elapsed:.1f}s)")
        else:
            print(f"FAIL  ({elapsed:.1f}s)")
            if tail:
                print(tail)
            all_ok = False

    print("=" * 64)
    if all_ok:
        print("[OK] All stages passed.")
        sys.exit(0)
    print("[FAIL] One or more stages failed. Fix the cause above before publishing/committing.")
    sys.exit(1)


if __name__ == "__main__":
    main()
