#!/usr/bin/env python3
"""
Install the git pre-commit hook for the FILDA Investment Execution Database.

Writes .git/hooks/pre-commit to run `python db/health.py --fast` (unit tests +
structural invariants). --fast is used so a code commit isn't blocked by an
unrelated data-checkpoint lag or transient network failure. Idempotent.

    python scripts/install_hooks.py
"""

import os
import stat
import sys

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_path = os.path.join(BASE_dir, ".git", "hooks", "pre-commit")

HOOK = """#!/bin/sh
# Installed by scripts/install_hooks.py
# Fast consistency gate: unit tests + structural invariants (no rebuild, no network).
python db/health.py --fast
result=$?
if [ $result -ne 0 ]; then
  echo ""
  echo "Pre-commit gate FAILED. Fix the issue above or commit with --no-verify"
  echo "(only if you understand why the gate is being bypassed)."
  exit $result
fi
"""


def main():
    if not os.path.isdir(os.path.join(BASE_dir, ".git")):
        sys.exit("Not a git repo (no .git dir). Run from the repo root.")
    os.makedirs(os.path.dirname(HOOK_path), exist_ok=True)
    with open(HOOK_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK)
    mode = os.stat(HOOK_path).st_mode
    os.chmod(HOOK_path, mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[OK] installed pre-commit hook -> {os.path.relpath(HOOK_path, BASE_dir)}")
    print("  runs: python db/health.py --fast")


if __name__ == "__main__":
    main()
