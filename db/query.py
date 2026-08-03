#!/usr/bin/env python3
"""
Read-only query CLI for the Angola Investment Execution Database.

A thin, dependency-free access layer over the SQLite database: filter projects
by sector / province / organization / FILDA edition / status / score range and
get JSON back. This is the embryo of the "workflow integration" leg of the
goal's framework (Unique Data + Decision Logic + Workflow Integration = Value)
— the data is only useful if something can query it.

Read-only: opens the DB in immutable mode and never writes.

Examples:
    python db/query.py                                   # all scored projects
    python db/query.py --sector Energy                   # one sector
    python db/query.py --province Bengo --min-score 60  # high-scoring Bengo
    python db/query.py --org Sonangol                    # everything Sonangol touches
    python db/query.py --edition 2024 --summary          # aggregate stats for 2024
    python db/query.py --project chicomba-water-dam      # full detail + timeline
    python db/query.py --facets                           # sector/province/edition browse counts

By default only scored projects (evidence_complete = 1) are returned, matching
the published figures. Pass --include-unscored to also return tracked-but-
unscored entries (e.g. Banco Sol).
"""

import argparse
import json
import os
import sqlite3
import sys

DB_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "investment_tracker.db")

# Fields selected for a project listing. Kept flat + JSON-friendly.
PROJECT_SELECT = """
    p.id, p.title, p.sector, p.subsector, p.province, p.municipality,
    p.status, p.execution_score, p.announced_value, p.currency,
    p.estimated_jobs, p.actual_completion, p.filda_edition,
    p.evidence_complete, p.last_verified, p.description, p.country,
    p.expected_completion, p.coordinates, p.created_at
"""


def connect():
    if not os.path.exists(DB_path):
        raise SystemExit(f"Database not found at {DB_path}. Run `python db/load.py`.")
    # Read-only + immutable: the query layer must never mutate the asset.
    return sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)


def project_row(row):
    return {
        "id": row["id"], "title": row["title"], "sector": row["sector"],
        "subsector": row["subsector"], "province": row["province"],
        "municipality": row["municipality"], "status": row["status"],
        "execution_score": row["execution_score"],
        "announced_value": row["announced_value"], "currency": row["currency"],
        "estimated_jobs": row["estimated_jobs"],
        "actual_completion": row["actual_completion"],
        "filda_edition": row["filda_edition"],
        "evidence_complete": row["evidence_complete"],
        "last_verified": row["last_verified"], "description": row["description"],
        "country": row["country"], "expected_completion": row["expected_completion"],
        "coordinates": row["coordinates"], "created_at": row["created_at"],
    }


def build_where(args):
    """Return (where_sql, params, joins) from the filter args."""
    clauses = []
    params = []
    joins = ""
    if not args.include_unscored:
        clauses.append("p.evidence_complete = 1")
    if args.sector:
        clauses.append("p.sector = ?")
        params.append(args.sector)
    if args.province:
        clauses.append("p.province = ?")
        params.append(args.province)
    if args.status:
        clauses.append("p.status = ?")
        params.append(args.status)
    if args.edition:
        clauses.append("p.filda_edition = ?")
        params.append(str(args.edition))
    if args.min_score is not None:
        clauses.append("p.execution_score >= ?")
        params.append(args.min_score)
    if args.max_score is not None:
        clauses.append("p.execution_score <= ?")
        params.append(args.max_score)
    if args.org:
        joins = (" JOIN project_organizations po ON po.project_id = p.id"
                 " JOIN organizations o ON o.id = po.organization_id")
        clauses.append("(o.name = ? OR o.id = ?)")
        params += [args.org, args.org]
    search = getattr(args, "search", None)
    if search:
        clauses.append("(p.title LIKE ? OR p.sector LIKE ? OR p.subsector LIKE ? OR p.province LIKE ?)")
        q = f"%{search}%"
        params += [q, q, q, q]
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params, joins


def query_projects(conn, args):
    where, params, joins = build_where(args)
    sql = (f"SELECT {PROJECT_SELECT} FROM projects p{joins}{where}"
           " GROUP BY p.id ORDER BY p.execution_score DESC, p.title")
    rows = conn.execute(sql, params).fetchall()
    out = [project_row(r) for r in rows]
    for p in out:
        p["organizations"] = [
            {"name": r["name"], "role": r["role"], "country": r["country"]}
            for r in conn.execute(
                "SELECT o.name, po.role, o.country FROM project_organizations po "
                "JOIN organizations o ON o.id = po.organization_id "
                "WHERE po.project_id = ?", (p["id"],))
        ]
    return out


def summary(conn, args):
    where, params, joins = build_where(args)
    base = f"FROM projects p{joins}{where}"
    n = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    if n == 0:
        return {"count": 0, "average_score": None, "distribution": {},
                "by_status": {}}
    avg = conn.execute(
        f"SELECT ROUND(AVG(execution_score), 2) {base}", params).fetchone()[0]
    from constants import score_distribution
    dist = score_distribution(
        s for (s,) in conn.execute(f"SELECT execution_score {base}", params))
    by_status = {r[0]: r[1] for r in conn.execute(
        f"SELECT status, COUNT(*) {base} GROUP BY status ORDER BY 2 DESC", params)}
    return {"count": n, "average_score": avg, "distribution": dist,
            "by_status": by_status}


def project_detail(conn, pid):
    p = conn.execute(f"SELECT {PROJECT_SELECT} FROM projects p "
                     "WHERE p.id = ?", (pid,)).fetchone()
    if p is None:
        return None
    d = project_row(p)
    d["organizations"] = [
        {"name": r["name"], "type": r["type"], "country": r["country"],
         "role": r["role"]}
        for r in conn.execute(
            "SELECT o.name, o.type, o.country, po.role "
            "FROM project_organizations po JOIN organizations o ON o.id = po.organization_id "
            "WHERE po.project_id = ? ORDER BY po.role", (pid,))
    ]
    d["events"] = [
        {"id": r["id"], "event_type": r["event_type"], "event_date": r["event_date"],
         "description": r["description"], "source_id": r["source_id"],
         "source_title": r["src_title"], "source_publisher": r["src_pub"],
         "source_url": r["src_url"], "source_confidence": r["src_conf"],
         "source_archived_url": r["src_arch"], "source_url_status": r["src_status"]}
        for r in conn.execute(
            "SELECT e.id, e.event_type, e.event_date, e.description, e.source_id, "
            "s.title AS src_title, s.publisher AS src_pub, s.url AS src_url, "
            "s.confidence AS src_conf, s.archived_url AS src_arch, s.url_status AS src_status "
            "FROM events e LEFT JOIN sources s ON s.id = e.source_id "
            "WHERE e.project_id = ? ORDER BY e.event_date", (pid,))
    ]
    d["field_evidence"] = [
        {"id": r["id"], "field": r["field"], "value": r["value"],
         "source_id": r["source_id"], "observed_at": r["observed_at"],
         "source_title": r["src_title"], "source_publisher": r["src_pub"],
         "source_url": r["src_url"]}
        for r in conn.execute(
            "SELECT pe.id, pe.field, pe.value, pe.source_id, pe.observed_at, "
            "s.title AS src_title, s.publisher AS src_pub, s.url AS src_url "
            "FROM project_evidence pe LEFT JOIN sources s ON s.id = pe.source_id "
            "WHERE pe.project_id = ? ORDER BY pe.id", (pid,))
    ]
    # Score breakdown
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from calculate_scores import calculate_score
    prow = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if prow:
        sc, breakdown = calculate_score(conn, prow)
        d["execution_score"] = sc
        d["score_breakdown"] = breakdown
    return d


def facets(conn):
    out = {}
    for dim in ("sector", "province", "filda_edition", "status"):
        rows = conn.execute(
            f"SELECT {dim} AS k, COUNT(*) AS n FROM projects "
            "WHERE evidence_complete = 1 AND {0} IS NOT NULL "
            "GROUP BY {0} ORDER BY n DESC".format(dim)
        ).fetchall()
        out[dim] = [{"value": r["k"], "count": r["n"]} for r in rows]
    return out


def main():
    ap = argparse.ArgumentParser(
        description="Read-only JSON query API over the Angola Investment Execution Database.")
    ap.add_argument("--sector")
    ap.add_argument("--province")
    ap.add_argument("--status")
    ap.add_argument("--edition", type=int)
    ap.add_argument("--org", help="organization name or id (any role)")
    ap.add_argument("--min-score", type=int, dest="min_score")
    ap.add_argument("--max-score", type=int, dest="max_score")
    ap.add_argument("--include-unscored", action="store_true",
                    help="also return tracked-but-unscored entries (default: scored only)")
    ap.add_argument("--search", help="full-text search across title, sector, subsector, province")
    ap.add_argument("--summary", action="store_true",
                    help="return aggregate stats over the filtered set instead of listing")
    ap.add_argument("--project", metavar="ID", help="return full detail for one project")
    ap.add_argument("--facets", action="store_true",
                    help="return browse counts by sector/province/edition/status")
    args = ap.parse_args()

    conn = connect()
    conn.row_factory = sqlite3.Row

    if args.facets:
        result = facets(conn)
    elif args.project:
        result = project_detail(conn, args.project)
        if result is None:
            raise SystemExit(f"No project with id {args.project!r}.")
    elif args.summary:
        result = summary(conn, args)
    else:
        result = query_projects(conn, args)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()