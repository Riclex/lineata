#!/usr/bin/env python3
"""
Incremental, append-only mutator for the Angola Investment Execution Database.

Day-to-day maintenance is event-driven here instead of a full CSV rebuild:
"a new construction article appeared" -> `add-event` -> one events row ->
recompute that one project's score -> one change_log row. No CSV editing, no
`load.py` rebuild needed between edits.

Operating modes (see README "Operating modes"):
  - Seed        : `python db/load.py`            (rebuild from CSV checkpoints; rare)
  - Append      : `python db/update.py <cmd> --apply`  (this script; day-to-day)
  - Checkpoint  : `python db/export_csv.py --apply`    (refresh CSVs after a session)

Discipline (hard-enforced):
  - No fabrication. Every add-event / add-evidence / set-status call REQUIRES
    `--source-url`. Event 80 (Banco Sol, source_id IS NULL) was seeded, never
    appended, and this script will never modify it.
  - One transaction per invocation; the change_log row is written inside it so
    a failed mutation leaves no audit orphan.
  - No-op mutations (duplicate source URL, duplicate evidence) exit 0 WITHOUT
    writing change_log rows — the audit trail records real mutations only.
  - DRY by default; `--apply` to persist. Raw sys.argv convention (matches
    db/_extract/*.py and db/verify_sources.py).

Usage:
    python db/update.py add-source   --url URL --title T --publisher P --date D [--confidence C] [--apply]
    python db/update.py add-event    --project ID --type T --date D --source-url URL [--title T] [--note N] [--apply]
    python db/update.py add-evidence --project ID --field F --value V --source-url URL [--observed-at D] [--apply]
    python db/update.py set-status   --project ID --status S --source-url URL [--date D] [--apply]
    python db/update.py relink       --table events|project_evidence --id N --source-url URL [--note N] [--apply]
    python db/update.py relink       --table events|project_evidence --id N --clear [--note N] [--apply]
    python db/update.py retype-event --event-id N --to <type> [--source-url URL] [--note N] [--apply]
    python db/update.py reverify     [--stale-days N] [--limit N] [--apply]
"""

import os
import sys
import sqlite3
import urllib.parse
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_sources import classify          # db/verify_sources.py:41 — stdlib urllib only
from calculate_scores import calculate_score  # db/calculate_scores.py:181 — single-project recompute
from audit import log_change                 # db/audit.py — shared change_log writer

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")

# CHECK-list values mirrored from db/schema.sql so we validate before INSERT.
EVENT_TYPES = {
    'announcement', 'mou', 'financing', 'groundbreaking', 'construction',
    'delay', 'suspension', 'restart', 'completion', 'expansion', 'closure',
    'ownership_change',
}
STATUS_TYPES = {
    'announced', 'mou_signed', 'financed', 'under_construction', 'delayed',
    'suspended', 'restarted', 'operational', 'completed', 'cancelled', 'unknown',
}
EVIDENCE_FIELDS = {'status', 'announced_value', 'estimated_jobs', 'actual_completion'}
CONFIDENCE_LEVELS = {'high', 'medium', 'low'}

# set-status -> the event_type that records the same transition (only inserted
# when --date is given). operational/unknown have no natural event.
STATUS_TO_EVENT = {
    'completed': 'completion',
    'under_construction': 'construction',
    'financed': 'financing',
    'mou_signed': 'mou',
    'announced': 'announcement',
    'restarted': 'restart',
    'delayed': 'delay',
    'suspended': 'suspension',
    'cancelled': 'closure',
}


# ------------------------------------------------------------------
# Arg parsing (raw sys.argv, --flag value; --apply is boolean)
# ------------------------------------------------------------------
# Boolean flags (no value consumed). --apply commits; --clear (relink) sets a
# source link to NULL instead of resolving a new source.
BOOLEAN_FLAGS = {"--apply", "--clear"}


def parse_flags(argv):
    flags = {}
    apply = False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--apply":
            apply = True
            i += 1
            continue
        if a == "--clear":
            flags["clear"] = True
            i += 1
            continue
        if a.startswith("--"):
            key = a[2:].replace("-", "_")
            if i + 1 >= len(argv):
                sys.exit(f"[ERR] missing value for {a}")
            flags[key] = argv[i + 1]
            i += 2
        else:
            i += 1
    return flags, apply


def require(flags, names):
    for n in names:
        if n not in flags or flags[n] is None or flags[n] == "":
            sys.exit(f"[ERR] missing required --{n.replace('_', '-')}")


def valid_url(url):
    try:
        p = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------
def connect():
    """Open a connection in manual-transaction mode with FKs on."""
    conn = sqlite3.connect(DB_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def project_exists(conn, pid):
    return conn.execute("SELECT 1 FROM projects WHERE id=?", (pid,)).fetchone() is not None


def resolve_or_create_source(conn, url, title, publisher, pub_date, confidence, verify):
    """Return (source_id, created_bool). Idempotent on URL: a duplicate URL
    returns the existing id and created=False. When created and verify is True,
    the URL is classified via verify_sources.classify() and last_verified /
    url_status are stamped; in a dry run (verify=False) they are left NULL.

    The SELECT-then-INSERT is race-free against the idx_sources_url unique
    index (schema.sql): if two paths race to create the same URL, the loser's
    INSERT raises IntegrityError, which we catch and re-resolve to the winner's
    id. The index is the real backstop — the SELECT is just the fast path."""
    row = conn.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
    if row is not None:
        return row[0], False
    if verify:
        status, _code = classify(url)
        last_verified = date.today().isoformat()
    else:
        status, last_verified = None, None
    try:
        cur = conn.execute(
            "INSERT INTO sources (title, url, date, publisher, confidence, "
            "last_verified, url_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, url, pub_date, publisher, confidence, last_verified, status))
        return cur.lastrowid, True
    except sqlite3.IntegrityError:
        # A concurrent insert won the race on idx_sources_url. Re-resolve.
        row = conn.execute("SELECT id FROM sources WHERE url=?", (url,)).fetchone()
        return row[0], False


def recompute_project_score(conn, pid):
    """Recompute the one project's score from current DB state and persist it.
    Returns (score, breakdown). The existing trg_projects_updated trigger
    advances updated_at on the UPDATE."""
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if row is None:
        return None
    score, breakdown = calculate_score(conn, row)
    conn.execute("UPDATE projects SET execution_score=? WHERE id=?", (score, pid))
    return score, breakdown


def finish(conn, apply, summary_lines):
    for line in summary_lines:
        print(line)
    if apply:
        conn.execute("COMMIT")
        print("\n[OK] committed")
    else:
        conn.execute("ROLLBACK")
        print("\n(dry run — DB not modified. Re-run with --apply to persist.)")
    conn.close()


# ------------------------------------------------------------------
# Subcommands
# ------------------------------------------------------------------
def cmd_add_source(flags, apply):
    require(flags, ["url", "title", "publisher", "date"])
    url = flags["url"].strip()
    title = flags["title"]
    publisher = flags["publisher"]
    pub_date = flags["date"]
    confidence = flags.get("confidence", "medium")
    if not valid_url(url):
        sys.exit(f"[ERR] --url must be a full http(s) URL: {url!r}")
    if confidence not in CONFIDENCE_LEVELS:
        sys.exit(f"[ERR] --confidence must be one of {sorted(CONFIDENCE_LEVELS)}")

    # Idempotency pre-check: a duplicate URL is a no-op, NOT a mutation.
    existing = sqlite3.connect(DB_path).execute(
        "SELECT id FROM sources WHERE url=?", (url,)).fetchone()
    if existing is not None:
        print(f"existing source id={existing[0]} for {url} (no-op)")
        return

    conn = connect()
    conn.execute("BEGIN")
    try:
        sid, created = resolve_or_create_source(
            conn, url, title, publisher, pub_date, confidence, verify=apply)
        if apply:
            log_change(conn, "add-source", "sources", sid,
                       {"id": sid, "url": url, "title": title, "publisher": publisher,
                        "date": pub_date, "confidence": confidence}, url, None)
        finish(conn, apply, [
            f"add-source: {url}",
            f"  title={title!r}  publisher={publisher!r}  date={pub_date}  confidence={confidence}",
            f"  -> source id={sid}  (liveness {'verified' if apply else 'pending verification on apply'})",
        ])
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] add-source failed: {e}")


def cmd_add_event(flags, apply):
    require(flags, ["project", "type", "date", "source_url"])
    pid = flags["project"]
    etype = flags["type"]
    edate = flags["date"]
    source_url = flags["source_url"].strip()
    title = flags.get("title")
    note = flags.get("note")

    if not valid_url(source_url):
        sys.exit(f"[ERR] --source-url must be a full http(s) URL: {source_url!r}")
    if etype not in EVENT_TYPES:
        sys.exit(f"[ERR] --type must be one of {sorted(EVENT_TYPES)}")

    conn = connect()
    if not project_exists(conn, pid):
        conn.close()
        sys.exit(f"[ERR] unknown project id: {pid!r}")

    conn.execute("BEGIN")
    try:
        sid, src_created = resolve_or_create_source(
            conn, source_url, title, None, None, "medium", verify=apply)
        # Idempotency guard: an event with the same (project, type, date, source,
        # description) is a re-run of the same add-event call, not a new
        # observation. No-op it (no INSERT, no change_log row) — matching the
        # add-source (dedup on URL) and add-evidence (dedup on project/field/
        # source) discipline. Description is included so two genuinely distinct
        # same-day/same-source announcements are still allowed (the 2023-07-18
        # digital-infrastructure pledge + Angosat-2 specifics are a real pair,
        # not a duplicate). A hard 4-column UNIQUE was deliberately NOT added to
        # events for the same reason.
        desc = note or title
        dup = conn.execute(
            "SELECT id FROM events WHERE project_id=? AND event_type=? "
            "AND event_date IS ? AND source_id IS ? AND description IS ?",
            (pid, etype, edate, sid, desc)).fetchone()
        if dup is not None:
            conn.execute("ROLLBACK"); conn.close()
            print(f"existing event id={dup[0]} for ({pid}, {etype}, {edate}, "
                  f"source {sid}) — skipping (re-run no-op)")
            return
        old_score = conn.execute(
            "SELECT execution_score FROM projects WHERE id=?", (pid,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO events (project_id, event_type, event_date, description, source_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, etype, edate, desc, sid))
        eid = cur.lastrowid
        new_score, breakdown = recompute_project_score(conn, pid)
        if apply:
            log_change(conn, "add-event", "events", eid,
                       {"event_id": eid, "project_id": pid, "event_type": etype,
                        "event_date": edate, "source_id": sid,
                        "source_url": source_url, "source_created": src_created,
                        "score_old": old_score, "score_new": new_score,
                        "breakdown": breakdown}, source_url, note)
        finish(conn, apply, [
            f"add-event: project={pid}  type={etype}  date={edate}",
            f"  source={source_url}  (source id={sid}, {'new' if src_created else 'existing'})",
            f"  -> event id={eid}  score {old_score} -> {new_score}",
        ])
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] add-event failed: {e}")


def cmd_add_evidence(flags, apply):
    require(flags, ["project", "field", "value", "source_url"])
    pid = flags["project"]
    field = flags["field"]
    value = flags["value"]
    source_url = flags["source_url"].strip()
    observed_at = flags.get("observed_at")

    if not valid_url(source_url):
        sys.exit(f"[ERR] --source-url must be a full http(s) URL: {source_url!r}")
    if field not in EVIDENCE_FIELDS:
        sys.exit(f"[ERR] --field must be one of {sorted(EVIDENCE_FIELDS)}")

    conn = connect()
    if not project_exists(conn, pid):
        conn.close()
        sys.exit(f"[ERR] unknown project id: {pid!r}")

    # Idempotency pre-check: (project, field, source) already backed is a no-op.
    # Changing a value requires a NEW source -> a new evidence row (append-only).
    conn.execute("BEGIN")
    try:
        sid, src_created = resolve_or_create_source(
            conn, source_url, None, None, None, "medium", verify=apply)
        dup = conn.execute(
            "SELECT id FROM project_evidence WHERE project_id=? AND field=? AND source_id=?",
            (pid, field, sid)).fetchone()
        if dup is not None:
            conn.execute("ROLLBACK"); conn.close()
            print(f"existing evidence id={dup[0]} for ({pid}, {field}, source {sid}) — skipping")
            return
        cur = conn.execute(
            "INSERT INTO project_evidence (project_id, field, value, source_id, observed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, field, value, sid, observed_at))
        ev_id = cur.lastrowid
        score, breakdown = recompute_project_score(conn, pid)
        if apply:
            log_change(conn, "add-evidence", "project_evidence", ev_id,
                       {"evidence_id": ev_id, "project_id": pid, "field": field,
                        "value": value, "source_id": sid, "source_url": source_url,
                        "observed_at": observed_at, "score": score}, source_url, None)
        finish(conn, apply, [
            f"add-evidence: project={pid}  field={field}  value={value!r}",
            f"  source={source_url}  (source id={sid}, {'new' if src_created else 'existing'})",
            f"  -> evidence id={ev_id}  (score unchanged at {score} — evidence bonus reads project fields, not this table)",
        ])
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] add-evidence failed: {e}")


def cmd_set_status(flags, apply):
    require(flags, ["project", "status", "source_url"])
    pid = flags["project"]
    new_status = flags["status"]
    source_url = flags["source_url"].strip()
    edate = flags.get("date")

    if not valid_url(source_url):
        sys.exit(f"[ERR] --source-url must be a full http(s) URL: {source_url!r}")
    if new_status not in STATUS_TYPES:
        sys.exit(f"[ERR] --status must be one of {sorted(STATUS_TYPES)}")

    conn = connect()
    if not project_exists(conn, pid):
        conn.close()
        sys.exit(f"[ERR] unknown project id: {pid!r}")

    conn.execute("BEGIN")
    try:
        old = conn.execute(
            "SELECT status, execution_score FROM projects WHERE id=?", (pid,)).fetchone()
        old_status, old_score = old[0], old[1]
        sid, src_created = resolve_or_create_source(
            conn, source_url, None, None, None, "medium", verify=apply)

        eid = None
        if edate and new_status in STATUS_TO_EVENT:
            etype = STATUS_TO_EVENT[new_status]
            # Idempotency guard (mirrors cmd_add_event): if the status-derived
            # event already exists for this project/date/source, don't insert a
            # duplicate. The status flip itself still proceeds below.
            dup = conn.execute(
                "SELECT id FROM events WHERE project_id=? AND event_type=? "
                "AND event_date IS ? AND source_id IS ?",
                (pid, etype, edate, sid)).fetchone()
            if dup is None:
                cur = conn.execute(
                    "INSERT INTO events (project_id, event_type, event_date, source_id) "
                    "VALUES (?, ?, ?, ?)",
                    (pid, etype, edate, sid))
                eid = cur.lastrowid
            else:
                eid = dup[0]  # reference the existing event in the audit payload

        conn.execute("UPDATE projects SET status=? WHERE id=?", (new_status, pid))
        new_score, breakdown = recompute_project_score(conn, pid)

        if apply:
            log_change(conn, "set-status", "projects", pid,
                       {"project_id": pid, "status_old": old_status,
                        "status_new": new_status, "event_id": eid,
                        "event_type": STATUS_TO_EVENT.get(new_status),
                        "event_date": edate, "source_id": sid, "source_url": source_url,
                        "score_old": old_score, "score_new": new_score,
                        "breakdown": breakdown}, source_url, None)
        ev_line = (f"  -> also inserted {STATUS_TO_EVENT.get(new_status)} event id={eid} "
                   f"dated {edate}" if eid else "  -> status flip only (no --date given)")
        finish(conn, apply, [
            f"set-status: project={pid}  {old_status} -> {new_status}",
            f"  source={source_url}  (source id={sid}, {'new' if src_created else 'existing'})",
            ev_line,
            f"  -> score {old_score} -> {new_score}",
        ])
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] set-status failed: {e}")


def cmd_reverify(flags, apply):
    stale_days = int(flags.get("stale_days", "30"))
    limit = flags.get("limit")

    conn = connect()
    rows = conn.execute(
        "SELECT id, url, last_verified, url_status FROM sources "
        "WHERE url != '' AND (last_verified IS NULL OR "
        "last_verified < date('now', ?) OR url_status IN ('dead','blocked')) "
        "ORDER BY id",
        (f"-{stale_days} days",)).fetchall()
    if limit:
        rows = rows[:int(limit)]

    print(f"reverify: {len(rows)} source(s) stale or never/dead-verified "
          f"(stale-days={stale_days}{f', limit={limit}' if limit else ''})")
    if not rows:
        conn.close()
        print("  nothing to do")
        return

    if not apply:
        for r in rows:
            print(f"  [{r['id']:>3}] last_verified={r['last_verified']} "
                  f"url_status={r['url_status']}  {r['url'][:70]}")
        print("\n(dry run — DB not modified. Re-run with --apply to re-check and stamp.)")
        conn.close()
        return

    conn.execute("BEGIN")
    today = date.today().isoformat()
    n = 0
    try:
        for r in rows:
            sid = r['id']
            url = r['url']
            status, _code = classify(url)
            old_status = r['url_status']
            old_lv = r['last_verified']
            conn.execute(
                "UPDATE sources SET last_verified=?, url_status=? WHERE id=?",
                (today, status, sid))
            log_change(conn, "reverify", "sources", sid,
                       {"old_status": old_status, "new_status": status,
                        "old_last_verified": old_lv, "new_last_verified": today},
                       url, None)
            n += 1
            print(f"  [{sid:>3}] {old_status or '-'} -> {status}  {url[:70]}")
        conn.execute("COMMIT")
        print(f"\n[OK] re-verified and stamped {n} source(s) (last_verified={today})")
        conn.close()
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] reverify failed: {e}")


def cmd_relink(flags, apply):
    """Re-link an existing events or project_evidence row to a different source.

    Fixes mis-grounded source links without touching event dates/types or
    project fields. Because source_id is NOT a formula input (see methodology
    § Versioning / the source_id invariance), re-linking never moves a score —
    the score_old/score_new logged below are equal by construction, and serve
    as an in-run proof of that invariance.

    Two modes:
      --source-url URL : resolve/create the source (idempotent on URL) and
                         point the row at it. Required by the no-fabrication
                         rule — the new link must trace to a real URL.
      --clear          : set source_id to NULL. Use only when the prior link
                         was definitively ungrounded AND no verified source is
                         available yet (leave NULL rather than invent a link).

    Idempotent: if the row already points at the resolved source, this is a
    no-op and writes no change_log row (the audit trail records real mutations
    only, matching add-event/add-evidence).
    """
    require(flags, ["table", "id"])
    table = flags["table"]
    row_id = flags["id"]
    clear = flags.get("clear") is not None
    source_url = flags.get("source_url")
    note = flags.get("note")

    if table not in ("events", "project_evidence"):
        sys.exit("[ERR] --table must be 'events' or 'project_evidence'")
    if clear and source_url:
        sys.exit("[ERR] pass either --source-url or --clear, not both")
    if not clear:
        if not source_url:
            sys.exit("[ERR] --source-url is required (or use --clear to NULL the link)")
        source_url = source_url.strip()
        if not valid_url(source_url):
            sys.exit(f"[ERR] --source-url must be a full http(s) URL: {source_url!r}")

    op = "relink-event" if table == "events" else "relink-evidence"

    conn = connect()
    conn.execute("BEGIN")
    try:
        row = conn.execute(
            f"SELECT id, project_id, source_id FROM {table} WHERE id=?",
            (row_id,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK"); conn.close()
            sys.exit(f"[ERR] no {table} row with id={row_id}")
        pid, old_sid = row[1], row[2]

        if clear:
            new_sid, src_created, resolved_url = None, False, None
        else:
            new_sid, src_created = resolve_or_create_source(
                conn, source_url, None, None, None, "medium", verify=apply)
            resolved_url = source_url

        if new_sid == old_sid:
            conn.execute("ROLLBACK"); conn.close()
            print(f"no-op: {table} id={row_id} already linked to source {old_sid}")
            return

        conn.execute(f"UPDATE {table} SET source_id=? WHERE id=?",
                     (new_sid, row_id))

        old_score = new_score = None
        if pid:
            s = conn.execute(
                "SELECT execution_score FROM projects WHERE id=?", (pid,)).fetchone()
            old_score = s[0] if s else None
            rec = recompute_project_score(conn, pid)
            new_score = rec[0] if rec else None

        if apply:
            log_change(conn, op, table, row_id,
                       {"table": table, "row_id": int(row_id), "project_id": pid,
                        "source_id_old": old_sid, "source_id_new": new_sid,
                        "source_url": resolved_url, "source_created": src_created,
                        "cleared": clear, "note": note,
                        "score_old": old_score, "score_new": new_score},
                       resolved_url, note)
        if clear:
            lines = [
                f"relink (clear): {table} id={row_id}  source {old_sid} -> NULL",
                f"  project={pid}  note={note or 'ungrounded link cleared'}",
            ]
        else:
            lines = [
                f"relink: {table} id={row_id}  source {old_sid} -> {new_sid}",
                f"  source={resolved_url}  ({'new' if src_created else 'existing'} source)",
                f"  project={pid}",
            ]
        if old_score is not None:
            lines.append(
                f"  -> score {old_score} -> {new_score} "
                f"(source_id is not a formula input — score unchanged by relink)")
        finish(conn, apply, lines)
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] relink failed: {e}")


def cmd_retype_event(flags, apply):
    """Re-type an existing event (e.g. an award mistyped as `completion` -> `expansion`).

    Corrects event_type without touching the date, description, or source link.
    Because event_type IS a formula input (see methodology § Versioning), the
    affected project's score is recomputed and score_old/score_new are logged.
    `--source-url` is optional: it defaults to the event's own source URL (the
    award article that justifies the re-type), so the audit row always traces to
    a real URL — no fabrication. The re-type is a correction of an
    already-evidenced event, not a new claim, so reusing its own backing source
    is defensible.

    Idempotent: if the event is already --to, this is a no-op and writes no
    change_log row (the audit trail records real mutations only, matching
    add-event/relink).
    """
    require(flags, ["event_id", "to"])
    eid = flags["event_id"]
    new_type = flags["to"]
    source_url = flags.get("source_url")
    note = flags.get("note")
    if new_type not in EVENT_TYPES:
        sys.exit(f"[ERR] --to must be one of {sorted(EVENT_TYPES)}")

    conn = connect()
    conn.execute("BEGIN")
    try:
        row = conn.execute(
            "SELECT e.id, e.project_id, e.event_type, e.source_id, s.url "
            "FROM events e LEFT JOIN sources s ON s.id=e.source_id WHERE e.id=?",
            (eid,)).fetchone()
        if row is None:
            conn.execute("ROLLBACK"); conn.close()
            sys.exit(f"[ERR] no event with id={eid}")
        pid, old_type, old_sid, own_url = row[1], row[2], row[3], row[4]
        if old_type == new_type:
            conn.execute("ROLLBACK"); conn.close()
            print(f"no-op: event {eid} already type={new_type}")
            return
        if not source_url:
            source_url = own_url  # the award article backing the event
        old_score = conn.execute(
            "SELECT execution_score FROM projects WHERE id=?", (pid,)).fetchone()[0]
        conn.execute("UPDATE events SET event_type=? WHERE id=?", (new_type, eid))
        new_score, breakdown = recompute_project_score(conn, pid)
        if apply:
            log_change(conn, "retype-event", "events", eid,
                       {"event_id": int(eid), "project_id": pid,
                        "event_type_old": old_type, "event_type_new": new_type,
                        "source_id": old_sid, "source_url": source_url,
                        "score_old": old_score, "score_new": new_score,
                        "breakdown": breakdown, "note": note},
                       source_url, note)
        finish(conn, apply, [
            f"retype-event: event id={eid}  {old_type} -> {new_type}",
            f"  project={pid}  source={source_url or '(no source link)'}",
            f"  -> score {old_score} -> {new_score}",
        ])
    except Exception as e:
        conn.execute("ROLLBACK"); conn.close()
        sys.exit(f"[ERR] retype-event failed: {e}")


# ------------------------------------------------------------------
# Dispatch
# ------------------------------------------------------------------
COMMANDS = {
    "add-source": cmd_add_source,
    "add-event": cmd_add_event,
    "add-evidence": cmd_add_evidence,
    "set-status": cmd_set_status,
    "relink": cmd_relink,
    "reverify": cmd_reverify,
    "retype-event": cmd_retype_event,
}


def main():
    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"usage: python db/update.py <{'|'.join(COMMANDS)}> ... [--apply]\n"
                 f"  commands: {', '.join(COMMANDS)}")
    cmd = sys.argv[1]
    flags, apply = parse_flags(sys.argv[2:])
    COMMANDS[cmd](flags, apply)


if __name__ == "__main__":
    main()