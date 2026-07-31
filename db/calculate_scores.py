#!/usr/bin/env python3
"""
Execution Score Calculator for Angola Investment Execution Database.

Reads from SQLite database, calculates execution scores based on the
methodology in docs/scoring-methodology.md, updates the execution_score
column, and prints a report of all scores.

Usage:
    python db/calculate_scores.py          # calculate and update DB
    python db/calculate_scores.py --dry    # calculate without updating DB
"""

import sqlite3
import os
import csv
import argparse
from datetime import datetime

# ============================================================
# Scoring constants (from docs/scoring-methodology.md)
# ============================================================

# Formula version. Bump this when ANY weight below changes, and record the
# change in docs/scoring-methodology.md § Versioning. Stored scores are a
# snapshot of a specific version; without this stamp, historical scores
# become unreproducible after a weight change (see limitation #14 in the
# methodology doc). load.py stamps this into db_meta.score_version on every
# rebuild, and verify.py asserts the DB row matches this constant.
SCORE_VERSION = "v1-2026-07"

BASE_SCORES = {
    'completed': 70,
    'operational': 60,
    'restarted': 45,
    'under_construction': 40,
    'financed': 35,
    'mou_signed': 25,
    'announced': 15,
    'delayed': 10,
    'suspended': 5,
    'cancelled': 0,
    'unknown': 10,
}

EVENT_POINTS = {
    'completion': 15,
    'expansion': 10,
    'groundbreaking': 8,
    'construction': 8,
    'financing': 7,
    'restart': 5,
    'mou': 5,
    'announcement': 3,
    'ownership_change': 0,
    'delay': 0,
    'suspension': 0,
    'closure': 0,
}

MAX_EVENT_POINTS = 30

STATUS_PENALTIES = {
    'delayed': -10,
    'suspended': -15,
    'unknown': -10,
}

# Delay penalty thresholds (years)
DELAY_PENALTIES = [
    (3, -15),
    (2, -10),
    (1, -5),
    (0, 0),
]


def parse_date(date_str):
    """Parse a partial date string (YYYY, YYYY-MM, or YYYY-MM-DD) to a datetime.

    Missing month/day default to January 1st. Returns None if unparseable.
    """
    if not date_str:
        return None
    s = date_str.strip()
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def years_between(date_str1, date_str2):
    """Calculate years between two date strings (YYYY, YYYY-MM, or YYYY-MM-DD)."""
    d1 = parse_date(date_str1)
    d2 = parse_date(date_str2)
    if d1 is None or d2 is None:
        return 0
    return (d2 - d1).days / 365.25


def calculate_event_points(conn, project_id):
    """Sum event points by DISTINCT event type (v2), capped at MAX_EVENT_POINTS.

    v2 scores each event type once, so duplicate coverage of the same milestone
    no longer stacks. A project with three `announcement` events earns +3, not
    +9. Rich, diverse timelines are still rewarded; repetitive coverage is not.
    See docs/scoring-methodology.md § Proposed v2 (adopted 2026-07-31).
    """
    types = {e[0] for e in conn.execute(
        "SELECT event_type FROM events WHERE project_id = ?", (project_id,))}
    total = sum(EVENT_POINTS.get(t, 0) for t in types)
    return min(total, MAX_EVENT_POINTS)


def calculate_evidence_bonus(conn, project):
    """Calculate evidence bonus based on concrete proof of delivery."""
    project_id = project['id']
    bonus = 0
    
    # Jobs created (verified number in project record)
    if project['estimated_jobs'] and project['estimated_jobs'] > 0:
        bonus += 3
    
    # Actual completion recorded
    if project['actual_completion'] and project['actual_completion'].strip():
        bonus += 3
    
    # Production-related events (construction, groundbreaking, completion)
    prod_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE project_id = ? AND event_type IN ('completion', 'construction', 'groundbreaking')",
        (project_id,)
    ).fetchone()[0]
    if prod_events > 0:
        bonus += 2
    
    # Awards won (expansion events can indicate awards/recognition)
    award_events = conn.execute(
        "SELECT COUNT(*) FROM events WHERE project_id = ? AND event_type = 'expansion'",
        (project_id,)
    ).fetchone()[0]
    if award_events > 0:
        bonus += 2
    
    return min(bonus, 10)


def calculate_delay_penalty(conn, project):
    """Calculate delay penalty based on years since first announcement."""
    # Get first announcement date
    first_event = conn.execute(
        "SELECT MIN(event_date) FROM events WHERE project_id = ? AND event_date IS NOT NULL",
        (project['id'],)
    ).fetchone()[0]
    
    if not first_event:
        return 0
    
    # Use actual_completion if available, otherwise use latest event or current date
    if project['actual_completion'] and project['actual_completion'].strip():
        end_date = project['actual_completion']
    else:
        latest_event = conn.execute(
            "SELECT MAX(event_date) FROM events WHERE project_id = ? AND event_date IS NOT NULL",
            (project['id'],)
        ).fetchone()[0]
        end_date = latest_event if latest_event else datetime.now().strftime('%Y-%m-%d')
    
    years = years_between(first_event, end_date)
    
    for threshold, penalty in DELAY_PENALTIES:
        if years >= threshold:
            return penalty
    
    return 0


def calculate_only_announcement_penalty(conn, project_id):
    """Penalty if only announcement events recorded (no follow-up)."""
    event_types = conn.execute(
        "SELECT DISTINCT event_type FROM events WHERE project_id = ?", (project_id,)
    ).fetchall()
    
    types = [e[0] for e in event_types]
    non_announcement = [t for t in types if t not in ('announcement',)]
    
    if len(non_announcement) == 0:
        return -10
    return 0


def calculate_score(conn, project):
    """Calculate the full execution score for a project.

    Projects flagged evidence_complete = 0 are *tracked but not scored* — they
    lack click-through evidence (see data-lineage.md "Event 80 (Banco Sol)").
    They get a score of 0 and are excluded from published averages so the
    headline figures never include an unverified claim. The goal's rule:
    "don't score projects unless someone can click through the evidence."
    """
    pid = project['id']
    ec = project['evidence_complete']
    ec = int(ec) if ec is not None else 1
    if ec == 0:
        return 0, {'base': 0, 'events': 0, 'evidence': 0, 'delay': 0,
                   'status_penalty': 0, 'only_announce': 0, 'unscored': True,
                   'version': SCORE_VERSION}
    status = project['status'] or 'unknown'
    
    # 1. Base score from status
    base = BASE_SCORES.get(status, 10)
    
    # 2. Event points
    event_pts = calculate_event_points(conn, pid)
    
    # 3. Evidence bonus
    evidence = calculate_evidence_bonus(conn, project)
    
    # 4. Delay penalty
    delay = calculate_delay_penalty(conn, project)
    
    # 5. Status penalty
    status_penalty = STATUS_PENALTIES.get(status, 0)
    
    # 6. Only-announcement penalty
    only_announce = calculate_only_announcement_penalty(conn, pid)
    
    # Total
    score = base + event_pts + evidence + delay + status_penalty + only_announce
    score = max(0, min(100, score))
    
    return score, {
        'base': base,
        'events': event_pts,
        'evidence': evidence,
        'delay': delay,
        'status_penalty': status_penalty,
        'only_announce': only_announce,
        'version': SCORE_VERSION,
    }


def compute_scores(conn):
    """Compute execution_score for every project from current DB state.

    Returns a dict {project_id: (score, breakdown)}. Pure read — does not
    write. Reused by main() and by db/load.py's post-load consistency check
    so the score stored in projects.csv is verified against the formula on
    every rebuild.
    """
    conn.row_factory = sqlite3.Row
    projects = conn.execute("SELECT * FROM projects ORDER BY title").fetchall()
    return {p['id']: calculate_score(conn, p) for p in projects}


def update_projects_csv(score_by_id):
    """Write computed scores back to data/projects.csv, preserving all other
    columns and row order. Used by --update-csv so the CSV snapshot of
    execution_score stays in sync with the formula instead of drifting.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base_dir, "data", "projects.csv")
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    changed = 0
    for row in rows:
        pid = row["id"]
        if pid in score_by_id:
            new = str(score_by_id[pid][0])
            if row.get("execution_score", "").strip() != new:
                row["execution_score"] = new
                changed += 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return changed


def main():
    parser = argparse.ArgumentParser(description='Calculate execution scores')
    parser.add_argument('--dry', action='store_true', help='Calculate without updating DB or CSV')
    parser.add_argument('--verbose', action='store_true', help='Show score breakdown')
    parser.add_argument('--update-csv', action='store_true',
                        help='Write computed scores back to data/projects.csv (keeps the CSV snapshot in sync)')
    args = parser.parse_args()

    db_path = os.path.join(os.path.dirname(__file__), 'investment_tracker.db')

    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    score_by_id = compute_scores(conn)
    projects = conn.execute("SELECT id, title, status FROM projects ORDER BY title").fetchall()
    scores = [(p['id'], p['title'], score_by_id[p['id']][0], p['status'], score_by_id[p['id']][1])
              for p in projects]

    print(f"Formula version: {SCORE_VERSION}")
    print(f"{'Project':<50} {'Score':>5}  Status")
    print("-" * 75)

    for pid, title, score, status, breakdown in scores:
        print(f"{title[:50]:<50} {score:>5}  {status or 'unknown'}")

        if args.verbose:
            b = breakdown
            print(f"  base={b['base']} events={b['events']:+d} evidence={b['evidence']:+d} "
                  f"delay={b['delay']:+d} status_pen={b['status_penalty']:+d} only_announce={b['only_announce']:+d}")

    # Summary stats — published figures cover SCORED projects only
    # (evidence_complete = 1). Tracked-but-unscored projects (score 0, no
    # click-through evidence) are kept in the DB but excluded from the
    # headline average so unverified claims never move the published number.
    scored = [s for s in scores if not s[4].get('unscored')]
    unscored = [s for s in scores if s[4].get('unscored')]
    avg = sum(s[2] for s in scored) / len(scored)
    print(f"\n{'='*75}")
    print(f"Projects tracked: {len(scores)}  |  scored: {len(scored)}  |  "
          f"unscored (no evidence): {len(unscored)}")
    if unscored:
        print(f"  unscored: {', '.join(s[0] for s in unscored)}")
    print(f"Average score (scored only): {avg:.1f}")
    print(f"Median score: {sorted(s[2] for s in scored)[len(scored)//2]}")
    print(f"Score range: {min(s[2] for s in scored)} - {max(s[2] for s in scored)}")

    # Distribution
    buckets = {'0-20': 0, '21-40': 0, '41-60': 0, '61-80': 0, '81-100': 0}
    for s in scored:
        if s[2] <= 20: buckets['0-20'] += 1
        elif s[2] <= 40: buckets['21-40'] += 1
        elif s[2] <= 60: buckets['41-60'] += 1
        elif s[2] <= 80: buckets['61-80'] += 1
        else: buckets['81-100'] += 1

    print(f"\nDistribution:")
    for bucket, count in buckets.items():
        bar = '#' * count
        print(f"  {bucket:>7}: {count:2d} {bar}")

    if args.dry:
        print(f"\n(dry run — database and CSV not updated)")
        conn.close()
        return

    # Update DB
    for pid, _, score, _, _ in scores:
        conn.execute("UPDATE projects SET execution_score = ? WHERE id = ?", (score, pid))
    conn.commit()
    print(f"\n[OK] Updated execution_score for {len(scores)} projects in database")

    # Optionally sync the CSV snapshot
    if args.update_csv:
        changed = update_projects_csv(score_by_id)
        print(f"[OK] data/projects.csv synced: {changed} score(s) rewritten")

    conn.close()


if __name__ == '__main__':
    main()