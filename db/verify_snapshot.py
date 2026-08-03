#!/usr/bin/env python3
"""
Snapshot verifier for the Angola Investment Execution Database.

Derives every published figure (counts, average, distribution, sector table,
gov-vs-private, case-study scores) FROM the database and compares it to a
committed baseline in db/snapshot.json. The baseline is git-tracked, so an
UNINTENDED drift (a CSV edit that moves a score) is caught against it; an
INTENDED change is made by regenerating with --update and committing.

It also pins the published article text: derives the figures from the DB and
asserts articles/*.md contain the matching numbers — a genuine article<->DB
pin (today's verify.py only checked DB == hardcoded expected).

Usage:
    python db/verify_snapshot.py          # compare DB to db/snapshot.json
    python db/verify_snapshot.py --update # regenerate db/snapshot.json from DB
"""

import json
import os
import re
import sys
import sqlite3

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_dir = os.path.dirname(os.path.abspath(__file__))
DB_path = os.path.join(DB_dir, "investment_tracker.db")
SNAPSHOT_path = os.path.join(DB_dir, "snapshot.json")
BASE_dir = os.path.dirname(DB_dir)
ARTICLES_dir = os.path.join(BASE_dir, "articles")
AVG_TOL = 0.05

CASE_STUDIES = [
    "huatong-angola-industry-awards", "linha-verde-investor-visas",
    "pt-ao-credit-line-2-5b", "pt-ao-credit-line-3-25b", "chicomba-water-dam",
    "investment-portal-georeferenced", "etu-energias-leao-ouro-2025",
]


def _distribution(scores):
    from constants import score_distribution
    return score_distribution(scores)


def generate_snapshot(conn):
    """Derive all published figures from the DB. Returns a JSON-serializable dict."""
    snap = {"counts": {}, "aggregate": {}, "sectors": {}, "gov_vs_private": {},
            "case_studies": {}}

    snap["counts"]["projects"] = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    snap["counts"]["scored"] = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE evidence_complete = 1").fetchone()[0]
    snap["counts"]["unscored"] = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE evidence_complete = 0").fetchone()[0]
    snap["counts"]["unscored_ids"] = sorted(r[0] for r in conn.execute(
        "SELECT id FROM projects WHERE evidence_complete = 0"))
    snap["counts"]["sources"] = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    snap["counts"]["events"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    snap["counts"]["events_linked"] = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NOT NULL").fetchone()[0]
    snap["counts"]["events_null"] = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NULL").fetchone()[0]
    snap["counts"]["null_event_ids"] = [r[0] for r in conn.execute(
        "SELECT id FROM events WHERE source_id IS NULL ORDER BY id")]

    scores = [r[0] for r in conn.execute(
        "SELECT execution_score FROM projects WHERE evidence_complete = 1")]
    avg = (sum(scores) / len(scores)) if scores else 0.0
    snap["aggregate"]["avg_precise"] = round(avg, 4)
    snap["aggregate"]["avg_rounded"] = int(round(avg))
    snap["aggregate"]["distribution"] = _distribution(scores)

    snap["sectors"] = {r[0]: {"count": r[1], "avg": r[2]} for r in conn.execute(
        "SELECT sector, COUNT(*), ROUND(AVG(execution_score), 1) "
        "FROM projects WHERE sector IS NOT NULL AND evidence_complete = 1 "
        "GROUP BY sector")}

    gov = [r[0] for r in conn.execute(
        "SELECT execution_score FROM projects WHERE sector='Government' AND evidence_complete=1")]
    priv = [r[0] for r in conn.execute(
        "SELECT execution_score FROM projects WHERE sector!='Government' AND evidence_complete=1")]
    snap["gov_vs_private"] = {
        "gov_count": len(gov),
        "private_count": len(priv),
        "gov_avg": round(sum(gov) / len(gov), 1) if gov else None,
        "private_avg": round(sum(priv) / len(priv), 1) if priv else None,
    }

    for pid in CASE_STUDIES:
        row = conn.execute(
            "SELECT execution_score FROM projects WHERE id = ?", (pid,)).fetchone()
        snap["case_studies"][pid] = row[0] if row else None
    return snap


def compare_snapshot(conn, expected):
    """Return a list of human-readable drift strings ([] if no drift)."""
    actual = generate_snapshot(conn)
    drifts = []

    def eq(label, a, e, tol=None):
        if tol is not None:
            if (a is None) != (e is None) or (a is not None and e is not None
                                             and abs(a - e) > tol):
                drifts.append(f"{label}: expected {e!r}, got {a!r}")
        elif a != e:
            drifts.append(f"{label}: expected {e!r}, got {a!r}")

    eq("counts.projects", actual["counts"]["projects"], expected["counts"]["projects"])
    eq("counts.scored", actual["counts"]["scored"], expected["counts"]["scored"])
    eq("counts.unscored", actual["counts"]["unscored"], expected["counts"]["unscored"])
    eq("counts.unscored_ids", actual["counts"]["unscored_ids"], expected["counts"]["unscored_ids"])
    eq("counts.sources", actual["counts"]["sources"], expected["counts"]["sources"])
    eq("counts.events", actual["counts"]["events"], expected["counts"]["events"])
    eq("counts.events_linked", actual["counts"]["events_linked"], expected["counts"]["events_linked"])
    eq("counts.events_null", actual["counts"]["events_null"], expected["counts"]["events_null"])
    eq("counts.null_event_ids", actual["counts"]["null_event_ids"], expected["counts"]["null_event_ids"])
    eq("aggregate.avg_precise", actual["aggregate"]["avg_precise"],
       expected["aggregate"]["avg_precise"], tol=AVG_TOL)
    eq("aggregate.avg_rounded", actual["aggregate"]["avg_rounded"], expected["aggregate"]["avg_rounded"])
    for bucket in ("0-20", "21-40", "41-60", "61-80", "81-100"):
        eq(f"distribution.{bucket}", actual["aggregate"]["distribution"][bucket],
           expected["aggregate"]["distribution"][bucket])
    for sector, e in expected["sectors"].items():
        a = actual["sectors"].get(sector)
        if a is None:
            drifts.append(f"sector '{sector}' missing from DB")
        else:
            eq(f"sector '{sector}' count", a["count"], e["count"])
            eq(f"sector '{sector}' avg", a["avg"], e["avg"], tol=AVG_TOL)
    for sector in actual["sectors"]:
        if sector not in expected["sectors"]:
            drifts.append(f"sector '{sector}' in DB but not in snapshot")
    eq("gov_vs_private.gov_count", actual["gov_vs_private"]["gov_count"],
       expected["gov_vs_private"]["gov_count"])
    eq("gov_vs_private.private_count", actual["gov_vs_private"]["private_count"],
       expected["gov_vs_private"]["private_count"])
    eq("gov_vs_private.gov_avg", actual["gov_vs_private"]["gov_avg"],
       expected["gov_vs_private"]["gov_avg"], tol=AVG_TOL)
    eq("gov_vs_private.private_avg", actual["gov_vs_private"]["private_avg"],
       expected["gov_vs_private"]["private_avg"], tol=AVG_TOL)
    for pid in CASE_STUDIES:
        eq(f"case_study {pid}", actual["case_studies"].get(pid), expected["case_studies"].get(pid))
    return drifts


def _article_files():
    out = []
    for root in (ARTICLES_dir,):
        for name in ("01-o-que-aconteceu-filda-pt.md", "01-what-happened-filda-en.md"):
            p = os.path.join(root, name)
            if os.path.exists(p):
                out.append(p)
        for sub in ("Substack", "LinkedIn"):
            p = os.path.join(root, sub, "01-what-happened-filda.md")
            if os.path.exists(p):
                out.append(p)
    return out


def check_articles(conn):
    """Derive headline figures from the DB and assert the article text contains
    them. Returns a list of drift strings ([] if all articles pin)."""
    snap = generate_snapshot(conn)
    avg_rounded = snap["aggregate"]["avg_rounded"]
    dist = snap["aggregate"]["distribution"]
    # The distribution is published as e.g. "10 / 1 / 7 / 20 / 12" or a table.
    # We assert each article contains the rounded avg string and each non-zero
    # bucket count as a standalone integer occurrence is too loose, so we check
    # the avg + each case-study score instead (robust against formatting).
    # Not every article cites every case study; only flag case-study scores
    # that ARE referenced by a distinctive slug fragment nearby. The earlier
    # `pid.split("-")[0]` heuristic matched common words ("pt" in Portuguese,
    # "investment" in English) and false-positived on articles that don't cite
    # the case study at all; use a per-case-study distinctive keyword instead.
    case_keywords = {
        "huatong-angola-industry-awards": "huatong",
        "linha-verde-investor-visas": "linha verde",
        "pt-ao-credit-line-2-5b": "2.5b",
        "pt-ao-credit-line-3-25b": "3.25b",
        "chicomba-water-dam": "chicomba",
        "investment-portal-georeferenced": "georeferenced",
        "etu-energias-leao-ouro-2025": "etu energias",
    }
    drifts = []
    for path in _article_files():
        with open(path, encoding="utf-8") as f:
            text = f.read()
        low = text.lower()
        if str(avg_rounded) not in text:
            drifts.append(f"{os.path.relpath(path, BASE_dir)}: avg score '{avg_rounded}' not found")
        for pid, sc in snap["case_studies"].items():
            if sc is not None and str(sc) not in text:
                # Skip absent ids: only flag when the article actually
                # references this case study by a distinctive slug fragment.
                kw = case_keywords.get(pid, pid.split("-")[0])
                if kw in low:
                    drifts.append(f"{os.path.relpath(path, BASE_dir)}: case study "
                                   f"{pid} score {sc} not found")
    return drifts


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Snapshot drift verifier.")
    ap.add_argument("--update", action="store_true",
                    help="regenerate db/snapshot.json from the current DB")
    args = ap.parse_args()

    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")
    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    if args.update:
        snap = generate_snapshot(conn)
        with open(SNAPSHOT_path, "w", encoding="utf-8") as f:
            json.dump(snap, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"[OK] wrote {os.path.relpath(SNAPSHOT_path, BASE_dir)} "
              f"(avg={snap['aggregate']['avg_precise']}, "
              f"{snap['counts']['projects']} projects)")
        conn.close()
        return

    if not os.path.exists(SNAPSHOT_path):
        conn.close()
        sys.exit(f"snapshot not found at {SNAPSHOT_path}. Run `python db/verify_snapshot.py --update`.")
    with open(SNAPSHOT_path, encoding="utf-8") as f:
        expected = json.load(f)

    drifts = compare_snapshot(conn, expected)
    article_drifts = check_articles(conn)
    conn.close()

    if drifts:
        print("[FAIL] DB has drifted from db/snapshot.json:")
        for d in drifts:
            print(f"  - {d}")
    if article_drifts:
        print("[FAIL] Article figures do not match the DB:")
        for d in article_drifts:
            print(f"  - {d}")
    if drifts or article_drifts:
        print("\nIf the change is intended, regenerate the baseline and update the articles:")
        print("  python db/verify_snapshot.py --update   # then commit db/snapshot.json")
        sys.exit(1)
    print("[OK] DB matches db/snapshot.json and article figures pin to the DB.")


if __name__ == "__main__":
    main()