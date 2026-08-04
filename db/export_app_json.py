#!/usr/bin/env python3
"""
Generate app/data.json from the DB — the static fallback app/index.html uses
when the live API (app/server.py) is unavailable (e.g. the page is opened as
file:// without the server running).

Reproduces the exact shape app/index.html consumes:
  {projects, events, orgs, evidence, breakdowns}
  - projects  : scored projects only (evidence_complete = 1), sorted by score
                DESC then title — mirrors /api/projects. Flat (organizations
                are NOT attached; they live in the `orgs` dict).
  - events    : dict keyed by project_id, over ALL projects (incl. tracked-but-
                unscored, which still has a timeline). Each event carries its
                source's src_* fields (now incl. src_archived_url + src_url_status
                so the timeline can route dead/blocked click-throughs to the archive).
  - orgs      : dict keyed by project_id, over ALL projects.
  - evidence  : dict keyed by project_id, over projects that have field evidence.
  - breakdowns: scored projects only (unscored has no formula breakdown).

Run after any DB mutation (db/update.py) so the static fallback never drifts
from the live DB — the file was previously hand-maintained, which left it stale
after edits. Read-only w.r.t. the DB; writes only app/data.json.

Usage:
    python db/export_app_json.py          # write app/data.json
    python db/export_app_json.py --check   # exit 1 if out of sync (CI guard)
"""

import json
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calculate_scores import compute_scores  # db/calculate_scores.py

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_path = os.path.join(BASE, "db", "investment_tracker.db")
OUT_path = os.path.join(BASE, "app", "data.json")

# Flat project fields the app reads. Includes description / coordinates /
# created_at — the detail view reads these; db/query.py's PROJECT_SELECT is
# narrower, so this list is the source of truth for the fallback shape.
# `updated_at` is deliberately excluded: load.py's rebuild re-INSERTs every row
# (advancing updated_at via the DEFAULT datetime('now')), so including it would
# make `--check` fail after every rebuild on a pure timestamp drift, not a data
# drift. The app does not read updated_at.
PROJECT_COLS = [
    "id", "title", "sector", "subsector", "description", "country", "province",
    "municipality", "coordinates", "status", "announced_value", "currency",
    "estimated_jobs", "expected_completion", "actual_completion", "execution_score",
    "filda_edition", "source_program", "last_verified", "evidence_complete", "created_at",
]


def _clean(d):
    return {k: ("" if v is None else v) for k, v in d.items()}


def build(conn):
    conn.row_factory = sqlite3.Row
    score_by_id = compute_scores(conn)

    # projects: scored only, sorted like /api/projects. execution_score is the
    # computed value (== stored; keeps app/CSV/DB in lockstep with the formula).
    prows = conn.execute(
        "SELECT * FROM projects WHERE evidence_complete = 1 "
        "ORDER BY execution_score DESC, title").fetchall()
    projects = []
    for r in prows:
        d = _clean({c: r[c] for c in PROJECT_COLS})
        d["execution_score"] = score_by_id[r["id"]][0]
        projects.append(d)

    # events: all projects, keyed by pid, ordered by event_date (as the app shows).
    events = {}
    for r in conn.execute(
        "SELECT e.*, s.title AS src_title, s.publisher AS src_pub, "
        "s.url AS src_url, s.confidence AS src_conf, "
        "s.archived_url AS src_archived_url, s.url_status AS src_url_status "
        "FROM events e LEFT JOIN sources s ON s.id = e.source_id "
        "ORDER BY e.project_id, e.event_date"):
        events.setdefault(r["project_id"], []).append(_clean({
            "id": r["id"], "project_id": r["project_id"], "event_type": r["event_type"],
            "event_date": r["event_date"], "description": r["description"],
            "source_id": r["source_id"],
            "src_title": r["src_title"], "src_pub": r["src_pub"], "src_url": r["src_url"],
            "src_conf": r["src_conf"], "src_archived_url": r["src_archived_url"],
            "src_url_status": r["src_url_status"],
        }))

    # orgs: all projects, keyed by pid, ordered by role (matches the detail view).
    orgs = {}
    for r in conn.execute(
        "SELECT po.project_id, po.role, o.name, o.type, o.country "
        "FROM project_organizations po JOIN organizations o ON o.id = po.organization_id "
        "ORDER BY po.project_id, po.role"):
        orgs.setdefault(r["project_id"], []).append({
            "project_id": r["project_id"], "role": r["role"],
            "name": r["name"], "type": r["type"], "country": r["country"]})

    # evidence: all projects that have field evidence, keyed by pid.
    evidence = {}
    for r in conn.execute(
        "SELECT pe.*, s.title AS src_title, s.publisher AS src_pub, s.url AS src_url "
        "FROM project_evidence pe LEFT JOIN sources s ON s.id = pe.source_id "
        "ORDER BY pe.project_id, pe.id"):
        evidence.setdefault(r["project_id"], []).append(_clean({
            "id": r["id"], "project_id": r["project_id"], "field": r["field"],
            "value": r["value"], "source_id": r["source_id"], "observed_at": r["observed_at"],
            "src_title": r["src_title"], "src_pub": r["src_pub"], "src_url": r["src_url"],
        }))

    # breakdowns: scored projects only (unscored is excluded from averages and
    # has no formula breakdown to display).
    breakdowns = {}
    for r in conn.execute("SELECT id FROM projects WHERE evidence_complete = 1"):
        bd = score_by_id[r["id"]][1]
        breakdowns[r["id"]] = {
            "base": bd["base"], "events": bd["events"], "evidence": bd["evidence"],
            "delay": bd["delay"], "status_penalty": bd["status_penalty"],
            "only_announce": bd["only_announce"], "version": bd["version"],
        }

    return {"projects": projects, "events": events, "orgs": orgs,
            "evidence": evidence, "breakdowns": breakdowns}


def main():
    check = "--check" in sys.argv
    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")
    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    data = build(conn)
    conn.close()
    body = json.dumps(data, ensure_ascii=False, indent=2)

    if check:
        existing = open(OUT_path, encoding="utf-8").read() if os.path.exists(OUT_path) else None
        if existing is not None and existing.rstrip() == body.rstrip():
            print("[OK] app/data.json is in sync with the DB")
            return
        print("[FAIL] app/data.json is out of sync with the DB "
              "— run `python db/export_app_json.py`")
        sys.exit(1)

    with open(OUT_path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"[OK] wrote {OUT_path}: {len(data['projects'])} projects, "
          f"{sum(len(v) for v in data['events'].values())} events, "
          f"{len(data['orgs'])} org-groups, {len(data['evidence'])} evidence-groups, "
          f"{len(data['breakdowns'])} breakdowns")


if __name__ == "__main__":
    main()