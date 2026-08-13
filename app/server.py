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
    GET /api/summary          — aggregate stats over the filtered set (+ 'dataset' global figures)
    GET /api/health           — DB row counts + checkpoint status
    GET /api/leads            — sign-up count + last-capture time (emails are NOT
                               exposed over the API; read data/leads.csv for them)
    POST /api/leads           — {email} -> appended to data/leads.csv (operator sink)

Leads hardening (env vars, no-op in dev):
    FILDA_LEADS_ORIGIN_ALLOWLIST  comma-separated Origins permitted to POST
                                 (unset = allow all; set = exact-match whitelist,
                                 a missing Origin header is rejected)
    FILDA_LEADS_RATE_MAX          per-IP submission cap (default 5)
    FILDA_LEADS_RATE_WINDOW       cap window in seconds (default 60)
"""

import csv
import json
import os
import sys
import time
import urllib.parse
from argparse import Namespace
from collections import defaultdict, deque
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_dir, "db"))

import sqlite3
from calculate_scores import SCORE_VERSION
import query as q

DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
APP_dir = os.path.join(BASE_dir, "app")
# Runtime capture sink for landing-page sign-ups. This is NOT part of the
# reproducible CSV->SQLite pipeline — it is gitignored runtime data the operator
# reads directly (the API exposes only a count, never the email addresses).
LEADS_path = os.path.join(BASE_dir, "data", "leads.csv")


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
        source_program=params.get("source_program"),
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


def append_lead(email):
    """Append a sign-up to data/leads.csv (the operator-visible sink).

    Runtime capture only — leads.csv is gitignored and is NOT part of the
    reproducible CSV->SQLite pipeline. Returns the new total lead count.
    """
    os.makedirs(os.path.dirname(LEADS_path), exist_ok=True)
    write_header = not os.path.exists(LEADS_path)
    captured_at = datetime.now().isoformat(timespec="seconds")
    with open(LEADS_path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["captured_at", "email"])
        w.writerow([captured_at, email])
    with open(LEADS_path, encoding="utf-8") as f:
        # subtract the header we may have just written
        return sum(1 for _ in f) - (1 if write_header else 0)


def leads_status():
    """Operator-visible count + last-capture timestamp. The email addresses
    themselves are never returned over the API — the operator reads
    data/leads.csv directly for those."""
    if not os.path.exists(LEADS_path):
        return {"count": 0, "last_lead_at": None}
    count = 0
    last = None
    with open(LEADS_path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r, None)  # header
        for row in r:
            if not row:
                continue
            count += 1
            last = row[0]
    return {"count": count, "last_lead_at": last}


# ---- POST /api/leads hardening (H2) -------------------------------------
# The leads endpoint writes to the operator's CSV sink from any requester, so
# it is the obvious abuse target. Two server-side gates, both no-ops in dev
# (unset env = allow all / generous limit) and only tightened in production:
#   1. Origin allowlist — reject cross-origin submissions from sites the
#      operator didn't list (FILDA_LEADS_ORIGIN_ALLOWLIST, comma-separated).
#   2. Per-IP sliding-window rate limit (FILDA_LEADS_RATE_MAX /
#      FILDA_LEADS_RATE_WINDOW seconds; defaults 5 / 60s).

def _parse_origin_allowlist():
    raw = os.environ.get("FILDA_LEADS_ORIGIN_ALLOWLIST", "").strip()
    if not raw:
        return ()
    return tuple(o.strip().lower() for o in raw.split(",") if o.strip())


LEADS_ORIGIN_ALLOWLIST = _parse_origin_allowlist()


def origin_allowed(origin):
    """True if `origin` may POST leads. Unset allowlist = dev (allow all).
    A set allowlist is an exact, case-insensitive match; a missing Origin
    header is NOT assumed safe once an allowlist is configured."""
    if not LEADS_ORIGIN_ALLOWLIST:
        return True
    if not origin:
        return False
    return origin.strip().lower() in LEADS_ORIGIN_ALLOWLIST


class RateLimiter:
    """Sliding-window per-IP limiter. Plain HTTPServer is single-threaded so
    the dict is accessed without a lock; switch to ThreadingHTTPServer and
    guard _hits with a Lock before relying on that."""

    def __init__(self, max_hits, window_seconds):
        self.max = max_hits
        self.window = window_seconds
        self._hits = defaultdict(deque)

    def check(self, ip, now=None):
        """Record a hit for `ip` and return True if under the limit, False if
        at/over it (and do NOT record). Pass `now` for deterministic tests."""
        if now is None:
            now = time.time()
        dq = self._hits[ip]
        cutoff = now - self.window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.max:
            return False
        dq.append(now)
        return True


def _rate_limiter_from_env():
    try:
        max_hits = int(os.environ.get("FILDA_LEADS_RATE_MAX", "5"))
        window = int(os.environ.get("FILDA_LEADS_RATE_WINDOW", "60"))
    except ValueError:
        max_hits, window = 5, 60
    return RateLimiter(max_hits, window)


RATE_LIMITER = _rate_limiter_from_env()


def handle_lead(email, client_ip, origin):
    """Pure handler for POST /api/leads. Returns (status, body_dict) so the
    HTTP layer just sends it — no socket machinery needed to unit-test.

    Origin and rate checks run BEFORE the CSV append, so a rejected request
    never writes to the operator's sink.
    """
    if not origin_allowed(origin):
        return 403, {"error": "Origin not allowed"}
    if not RATE_LIMITER.check(client_ip or "unknown"):
        return 429, {"error": "Too many requests"}
    if not email or "@" not in email or "." not in email:
        return 400, {"error": "A valid email is required"}
    count = append_lead(email)
    return 200, {"ok": True, "count": count}


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
            if path == "/api/leads":
                self._send_json(leads_status()); return
            if path == "/api/facets":
                conn = connect(); self._send_json(q.facets(conn)); conn.close(); return
            if path == "/api/summary":
                conn = connect()
                resp = q.summary(conn, _ns(params))
                resp["dataset"] = q.dataset_stats(conn)  # global sidebar figures
                self._send_json(resp); conn.close(); return
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

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/leads":
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else ""
                try:
                    payload = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    self._send_error(400, "Invalid JSON body"); return
                email = (payload.get("email") or "").strip()
                # H2: origin allowlist + per-IP rate limit run inside handle_lead,
                # BEFORE the CSV append — a rejected request never writes.
                status, body = handle_lead(
                    email, self.client_address[0], self.headers.get("Origin"))
                self._send_json(body, status); return
            self._send_error(404, "Not found"); return
        except Exception as e:
            self._send_server_error(e); return

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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
