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
from argparse import Namespace
from http.server import HTTPServer, SimpleHTTPRequestHandler

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_dir, "db"))

import sqlite3
from calculate_scores import SCORE_VERSION
import query as q

DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
APP_dir = os.path.join(BASE_dir, "app")


def connect():
    if not os.path.exists(DB_path):
        raise FileNotFoundError(f"Database not found at {DB_path}")
    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ns(params):
    """Build a duck-typed namespace from a query-string dict for query.py."""
    def _int(v):
        return int(v) if v not in (None, "") else None
    return Namespace(
        sector=params.get("sector"), province=params.get("province"),
        status=params.get("status"),
        edition=str(params.get("edition")) if params.get("edition") else None,
        org=params.get("org"),
        min_score=_int(params.get("min_score")), max_score=_int(params.get("max_score")),
        search=params.get("search"), include_unscored=bool(params.get("include_unscored")),
        summary=False, project=None, facets=False,
    )


def get_health():
    conn = connect()
    tables = ["projects", "organizations", "project_organizations", "events",
              "project_evidence", "sources", "change_log"]
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    le = conn.execute("SELECT value FROM db_meta WHERE key='last_exported_at'").fetchone()
    sv = conn.execute("SELECT value FROM db_meta WHERE key='score_version'").fetchone()
    conn.close()
    return {"counts": counts, "last_exported_at": le[0] if le else None,
            "score_version": sv[0] if sv else None, "formula_version": SCORE_VERSION}


class APIHandler(SimpleHTTPRequestHandler):
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

    def _send_server_error(self, exc):
        """Log the real exception, return a generic message to the client."""
        # Never leak raw Python exception text to API consumers.
        self.log_message("unhandled %s: %s", type(exc).__name__, exc)
        self._send_json({"error": "Internal server error"}, 500)

    def _params(self):
        return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        params = self._params()
        try:
            if path == "/api/health":
                self._send_json(get_health()); return
            if path == "/api/facets":
                conn = connect(); self._send_json(q.facets(conn)); conn.close(); return
            if path == "/api/summary":
                conn = connect(); self._send_json(q.summary(conn, _ns(params))); conn.close(); return
            if path == "/api/projects":
                conn = connect(); self._send_json(q.query_projects(conn, _ns(params))); conn.close(); return
            if path.startswith("/api/projects/"):
                pid = path.split("/api/projects/", 1)[1].strip("/")
                if not pid:
                    self._send_error(400, "Missing project ID"); return
                conn = connect()
                detail = q.project_detail(conn, pid)
                conn.close()
                if detail is None:
                    self._send_error(404, f"Project not found: {pid}")
                else:
                    self._send_json(detail)
                return
        except Exception as e:
            self._send_server_error(e); return
        super().do_GET()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        if "/api/" in (args[0] if args else ""):
            super().log_message(format, *args)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FILDA API + static server")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db-only", action="store_true")
    args = parser.parse_args()
    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")
    server = HTTPServer(("0.0.0.0", args.port), APIHandler)
    print(f"FILDA Investment Tracker — API server")
    print(f"  Static files : {'disabled' if args.db_only else 'app/'}")
    print(f"  API base     : http://localhost:{args.port}/api/")
    print(f"  App          : http://localhost:{args.port}/")
    print(f"  Health       : http://localhost:{args.port}/api/health")
    print(f"  Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down."); server.shutdown()


if __name__ == "__main__":
    main()
