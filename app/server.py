#!/usr/bin/env python3
"""
Zero-dependency API server for the Angola Investment Execution Database.

Serves the app/ static files and wraps db/query.py as JSON endpoints.
Stdlib only — matches the project's philosophy.

Usage:
    python app/server.py              # start on port 8080
    python app/server.py --port 3000  # custom port
    python app/server.py --db-only    # API only, no static files (for CORS dev)

Endpoints:
    GET /api/projects         — list all scored projects (supports query.py filters)
    GET /api/projects/<id>    — full detail for one project (timeline, orgs, evidence, breakdown)
    GET /api/facets           — browse counts by sector/province/edition/status
    GET /api/summary          — aggregate stats over the filtered set
    GET /api/health           — DB row counts + checkpoint status
"""

import json
import os
import sys
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

# Add db/ to the path so we can import the pipeline modules.
BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_dir, "db"))

import sqlite3
from calculate_scores import calculate_score, SCORE_VERSION

DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
APP_dir = os.path.join(BASE_dir, "app")

PROJECT_SELECT = """
    p.id, p.title, p.sector, p.subsector, p.province, p.municipality,
    p.status, p.execution_score, p.announced_value, p.currency,
    p.estimated_jobs, p.actual_completion, p.filda_edition,
    p.evidence_complete, p.last_verified, p.description, p.country,
    p.expected_completion, p.coordinates, p.created_at
"""


def connect():
    if not os.path.exists(DB_path):
        raise FileNotFoundError(f"Database not found at {DB_path}")
    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def project_row(row):
    return {k: row[k] for k in row.keys()}


def query_projects(params):
    """List projects with optional filters. Mirrors db/query.py query_projects()."""
    conn = connect()
    clauses = ["p.evidence_complete = 1"]
    query_params = []
    joins = ""

    sector = params.get("sector")
    province = params.get("province")
    status = params.get("status")
    edition = params.get("edition")
    org = params.get("org")
    min_score = params.get("min_score")
    max_score = params.get("max_score")
    search = params.get("search")

    if sector:
        clauses.append("p.sector = ?"); query_params.append(sector)
    if province:
        clauses.append("p.province = ?"); query_params.append(province)
    if status:
        clauses.append("p.status = ?"); query_params.append(status)
    if edition:
        clauses.append("p.filda_edition = ?"); query_params.append(str(edition))
    if min_score is not None:
        clauses.append("p.execution_score >= ?"); query_params.append(int(min_score))
    if max_score is not None:
        clauses.append("p.execution_score <= ?"); query_params.append(int(max_score))
    if org:
        joins = (" JOIN project_organizations po ON po.project_id = p.id"
                 " JOIN organizations o ON o.id = po.organization_id")
        clauses.append("(o.name = ? OR o.id = ?)")
        query_params += [org, org]
    if search:
        clauses.append("(p.title LIKE ? OR p.sector LIKE ? OR p.subsector LIKE ? OR p.province LIKE ?)")
        q = f"%{search}%"
        query_params += [q, q, q, q]

    where = " WHERE " + " AND ".join(clauses)
    sql = (f"SELECT {PROJECT_SELECT} FROM projects p{joins}{where}"
           " GROUP BY p.id ORDER BY p.execution_score DESC, p.title")
    rows = conn.execute(sql, query_params).fetchall()
    projects = [project_row(r) for r in rows]

    # Attach org names for list display
    for p in projects:
        p["organizations"] = [
            {"name": r["name"], "role": r["role"], "country": r["country"]}
            for r in conn.execute(
                "SELECT o.name, po.role, o.country FROM project_organizations po "
                "JOIN organizations o ON o.id = po.organization_id "
                "WHERE po.project_id = ?", (p["id"],))
        ]

    conn.close()
    return projects


def project_detail(pid):
    """Full detail for one project: timeline, orgs, evidence, score breakdown."""
    conn = connect()
    p = conn.execute(f"SELECT {PROJECT_SELECT} FROM projects p WHERE p.id = ?", (pid,)).fetchone()
    if p is None:
        conn.close()
        return None

    detail = project_row(p)

    # Organizations
    detail["organizations"] = [
        {"name": r["name"], "type": r["type"], "country": r["country"], "role": r["role"]}
        for r in conn.execute(
            "SELECT o.name, o.type, o.country, po.role "
            "FROM project_organizations po JOIN organizations o ON o.id = po.organization_id "
            "WHERE po.project_id = ? ORDER BY po.role", (pid,))
    ]

    # Events with source info
    detail["events"] = [
        {"id": r["id"], "event_type": r["event_type"], "event_date": r["event_date"],
         "description": r["description"], "source_id": r["source_id"],
         "source_title": r["src_title"], "source_publisher": r["src_pub"],
         "source_url": r["src_url"], "source_confidence": r["src_conf"],
         "source_archived_url": r["src_archived_url"],
         "source_url_status": r["src_url_status"]}
        for r in conn.execute(
            "SELECT e.*, s.title AS src_title, s.publisher AS src_pub, "
            "s.url AS src_url, s.confidence AS src_conf, "
            "s.archived_url AS src_archived_url, s.url_status AS src_url_status "
            "FROM events e LEFT JOIN sources s ON s.id = e.source_id "
            "WHERE e.project_id = ? ORDER BY e.event_date", (pid,))
    ]

    # Field evidence
    detail["field_evidence"] = [
        {"id": r["id"], "field": r["field"], "value": r["value"],
         "source_id": r["source_id"], "observed_at": r["observed_at"],
         "source_title": r["src_title"], "source_publisher": r["src_pub"],
         "source_url": r["src_url"]}
        for r in conn.execute(
            "SELECT pe.*, s.title AS src_title, s.publisher AS src_pub, s.url AS src_url "
            "FROM project_evidence pe LEFT JOIN sources s ON s.id = pe.source_id "
            "WHERE pe.project_id = ? ORDER BY pe.id", (pid,))
    ]

    # Score breakdown
    project_row_obj = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    if project_row_obj:
        score, breakdown = calculate_score(conn, project_row_obj)
        detail["execution_score"] = score
        detail["score_breakdown"] = breakdown

    conn.close()
    return detail


def get_facets():
    """Browse counts by sector/province/edition/status."""
    conn = connect()
    out = {}
    for dim in ("sector", "province", "filda_edition", "status"):
        rows = conn.execute(
            f"SELECT {dim} AS k, COUNT(*) AS n FROM projects "
            "WHERE evidence_complete = 1 AND {0} IS NOT NULL "
            "GROUP BY {0} ORDER BY n DESC".format(dim)
        ).fetchall()
        out[dim] = [{"value": r["k"], "count": r["n"]} for r in rows]
    conn.close()
    return out


def get_summary(params):
    """Aggregate stats over the filtered set."""
    conn = connect()
    clauses = ["p.evidence_complete = 1"]
    query_params = []
    joins = ""

    for key in ("sector", "province", "status", "edition"):
        val = params.get(key)
        if val:
            clauses.append(f"p.{key} = ?"); query_params.append(val)

    where = " WHERE " + " AND ".join(clauses)
    base = f"FROM projects p{joins}{where}"

    n = conn.execute(f"SELECT COUNT(*) {base}", query_params).fetchone()[0]
    if n == 0:
        conn.close()
        return {"count": 0, "average_score": None, "distribution": {}, "by_status": {}}

    avg = conn.execute(
        f"SELECT ROUND(AVG(execution_score), 2) {base}", query_params).fetchone()[0]

    dist = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for (s,) in conn.execute(f"SELECT execution_score {base}", query_params):
        if s <= 20: dist["0-20"] += 1
        elif s <= 40: dist["21-40"] += 1
        elif s <= 60: dist["41-60"] += 1
        elif s <= 80: dist["61-80"] += 1
        else: dist["81-100"] += 1

    by_status = {r[0]: r[1] for r in conn.execute(
        f"SELECT status, COUNT(*) {base} GROUP BY status ORDER BY 2 DESC", query_params)}

    conn.close()
    return {"count": n, "average_score": avg, "distribution": dist, "by_status": by_status}


def get_health():
    """DB row counts + checkpoint status."""
    conn = connect()
    tables = ["projects", "organizations", "project_organizations", "events",
              "project_evidence", "sources", "change_log"]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    le = conn.execute("SELECT value FROM db_meta WHERE key='last_exported_at'").fetchone()
    sv = conn.execute("SELECT value FROM db_meta WHERE key='score_version'").fetchone()
    conn.close()
    return {
        "counts": counts,
        "last_exported_at": le[0] if le else None,
        "score_version": sv[0] if sv else None,
        "formula_version": SCORE_VERSION,
    }


class APIHandler(SimpleHTTPRequestHandler):
    """Serves static files from app/ and JSON from /api/ endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=APP_dir, **kwargs)

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status)

    def _parse_params(self):
        parsed = urllib.parse.urlparse(self.path)
        return dict(urllib.parse.parse_qsl(parsed.query))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        params = self._parse_params()

        # API routes
        if path == "/api/health":
            try:
                self._send_json(get_health())
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path == "/api/facets":
            try:
                self._send_json(get_facets())
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path == "/api/summary":
            try:
                self._send_json(get_summary(params))
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path == "/api/projects":
            try:
                self._send_json(query_projects(params))
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path.startswith("/api/projects/"):
            pid = path.split("/api/projects/", 1)[1].strip("/")
            if not pid:
                self._send_error(400, "Missing project ID")
                return
            try:
                detail = project_detail(pid)
                if detail is None:
                    self._send_error(404, f"Project not found: {pid}")
                else:
                    self._send_json(detail)
            except Exception as e:
                self._send_error(500, str(e))
            return

        # Fall through to static file serving
        super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        # Quieter logging: only show API requests
        if "/api/" in (args[0] if args else ""):
            super().log_message(format, *args)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FILDA API + static server")
    parser.add_argument("--port", type=int, default=8080, help="Port to listen on (default: 8080)")
    parser.add_argument("--db-only", action="store_true", help="API only, no static files")
    args = parser.parse_args()

    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")

    handler = APIHandler
    server = HTTPServer(("0.0.0.0", args.port), handler)

    print(f"FILDA Investment Tracker — API server")
    print(f"  Static files : {'disabled' if args.db_only else 'app/'}")
    print(f"  API base     : http://localhost:{args.port}/api/")
    print(f"  App          : http://localhost:{args.port}/")
    print(f"  Health       : http://localhost:{args.port}/api/health")
    print(f"  Press Ctrl+C to stop.")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
