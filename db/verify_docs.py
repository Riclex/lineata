#!/usr/bin/env python3
"""
Doc-figure drift detector for the Angola Investment Execution Database.

db/verify_invariants.py pins the *structural* contract and db/verify_snapshot.py
pins the *article* figures to the DB snapshot, but the *docs* and README cite
the same kind of numbers by hand — and that is how a worked example goes stale
(e.g. scoring-methodology.md's Huatong example read 83 for a week after the
score moved to 85). This script scans docs/*.md + README.md for cited numbers
and compares them to the DB (and, for the contract check count, to the
verify_invariants.py + verify_snapshot.py reported summaries). It is a best-effort
regex sweep, not a full contract — a FAIL means a doc cites a number the DB
contradicts. The data-lineage.md checks anchor on present-tense phrasing (the
Source Layer "broadened-DB figures are:" summary, the Scoring Layer avg +
distribution table, the Gov vs private row) so the historical "Figure cascade"
entries — which legitimately retain older numbers — can't satisfy them.

Read-only; runs db/verify_invariants.py and db/verify_snapshot.py as subprocesses
to get the authoritative check count (it does not parse their source, which is
loop-driven).

Usage:
    python db/verify_docs.py
"""

import os
import re
import subprocess
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_path = os.path.join(BASE_dir, "db", "investment_tracker.db")
DOCS = os.path.join(BASE_dir, "docs")
README = os.path.join(BASE_dir, "README.md")
EXTRACT_README = os.path.join(BASE_dir, "db", "_extract", "README.md")
SCORING = os.path.join(DOCS, "scoring-methodology.md")
LINEAGE = os.path.join(DOCS, "data-lineage.md")
PY = sys.executable

sys.path.insert(0, os.path.join(BASE_dir, "db"))
from constants import SCORE_BUCKETS, score_distribution  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def first_int(text, pattern):
    """First integer captured by `pattern` in text, or None."""
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def first_float(text, pattern):
    """First float captured by `pattern` in text, or None."""
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def verify_check_count():
    """Run db/verify_invariants.py + db/verify_snapshot.py and sum their
    'NN checks' / passed summary lines into one total check count."""
    total = 0
    for script in ("verify_invariants.py", "verify_snapshot.py"):
        r = subprocess.run([PY, os.path.join("db", script)], cwd=BASE_dir,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        # verify_invariants prints "NN checks, ..."; verify_snapshot prints
        # "[OK] ..." (no check count) — count its drifts as 0 on success.
        m = re.search(r"(\d+) checks,\s+\d+ passed", r.stdout + r.stderr)
        if m:
            total += int(m.group(1))
    return total if total else None


def run_checks(conn, vchk=None):
    """Run all doc-figure drift checks against `conn`. Returns the `checks`
    list of (label, ok, expected, actual, location). Reads doc files from the
    module globals (README, EXTRACT_README, DOCS, LINEAGE, SCORING); if `vchk`
    is None it calls verify_check_count() (subprocess). Pure read — does not
    close `conn` or exit; main() owns those. Extracted from main() so the
    verifier is unit-testable against an in-memory fixture (F19: the verifiers
    had no self-tests, so an inverted regex or wrong capture group was
    invisible until it false-greened/red against live data).
    """
    # Authoritative DB values.
    n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    n_events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    n_linked = conn.execute(
        "SELECT COUNT(*) FROM events WHERE source_id IS NOT NULL").fetchone()[0]
    avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects WHERE evidence_complete = 1"
    ).fetchone()[0]
    avg_rounded = round(avg, 4)
    filda_avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects "
        "WHERE evidence_complete = 1 AND source_program = 'FILDA'").fetchone()[0]
    # F5: de-hardcode the "NN scored" and "gov/private NN,N vs NN,N" literals so
    # the checks hold at any dataset size (the count/gov-avg now come from the DB,
    # not a baked-in "103" / "54,0"). F7: extra current-state figures the docs cite.
    n_scored = conn.execute(
        "SELECT COUNT(*) FROM projects WHERE evidence_complete = 1").fetchone()[0]
    gov_avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects "
        "WHERE evidence_complete = 1 AND sector = 'Government'").fetchone()[0]
    n_po = conn.execute("SELECT COUNT(*) FROM project_organizations").fetchone()[0]
    # Edition averages (scored only) — cited in the Limitations #5 prose.
    def edition_avg(ed):
        r = conn.execute(
            "SELECT AVG(execution_score) FROM projects "
            "WHERE evidence_complete = 1 AND filda_edition = ?", (ed,)).fetchone()
        return r[0]
    ed22 = edition_avg("2022")
    ed26 = edition_avg("2026")
    # Investment Portal score (Article Layer case-study mention).
    ip = conn.execute(
        "SELECT execution_score FROM projects "
        "WHERE id = 'investment-portal-georeferenced'").fetchone()
    ip_score = ip[0] if ip else None
    # FILDA-only sector averages (Article Layer sector-avg line). Store the RAW
    # average; the check compares the doc's 1-dp value to it with a tolerance
    # that accepts either nearest rounding (a .5 boundary like 57.25 is valid as
    # 57,2 or 57,3 — a rounding-convention choice, not drift).
    filda_sector = {}
    for r in conn.execute(
        "SELECT sector, AVG(execution_score) a FROM projects "
        "WHERE evidence_complete = 1 AND source_program = 'FILDA' "
        "GROUP BY sector"):
        filda_sector[r["sector"]] = r["a"]

    if vchk is None:
        vchk = verify_check_count()

    checks = []  # (label, ok, expected, actual, location)

    def chk(label, expected, actual, location, ok=None):
        if expected is None:
            checks.append((label, False, expected, actual, location))
            return
        ok = (expected == actual) if ok is None else ok
        checks.append((label, ok, expected, actual, location))

    # --- verify_invariants.py check count cited in README + _extract/README ---
    readme = read(README)
    chk("README 'verify_invariants.py (NN checks)'", vchk,
        first_int(readme, r"verify_invariants\.py[^\n]*\((\d+) checks"), "README.md")
    if os.path.exists(EXTRACT_README):
        er = read(EXTRACT_README)
        chk("_extract/README 'NN structural invariant checks'", vchk,
            first_int(er, r"# (\d+) structural"), "db/_extract/README.md")

    # --- getting-started.md invariant-count citation (new file coverage) ---
    gs_path = os.path.join(DOCS, "getting-started.md")
    if os.path.exists(gs_path):
        gs = read(gs_path)
        chk("getting-started 'verify_invariants.py (NN checks)'", vchk,
            first_int(gs, r"verify_invariants\.py[^\n]*\((\d+) checks"),
            "docs/getting-started.md")

    # --- data-lineage.md cited counts ---
    lineage = read(LINEAGE)
    chk("data-lineage '### NNN sources in the database'", n_sources,
        first_int(lineage, r"### (\d+) sources in the database"), "docs/data-lineage.md")
    chk("data-lineage counts table '| events | N |'", n_events,
        first_int(lineage, r"\| events \| (\d+) \|"), "docs/data-lineage.md")
    chk("data-lineage counts table '| sources | N |'", n_sources,
        first_int(lineage, r"\| sources \| (\d+) \|"), "docs/data-lineage.md")
    chk("data-lineage 'NN linked / 1 NULL'", n_linked,
        first_int(lineage, r"(\d+) linked / 1 NULL"), "docs/data-lineage.md")
    chk("data-lineage counts table '| project_organizations | N |'", n_po,
        first_int(lineage, r"\| project_organizations \| (\d+) \|"),
        "docs/data-lineage.md")
    lin_avg = first_float(lineage, r"avg (\d+\.\d+) over the 50 scored")
    # Allow the lineage avg to be one- or two-decimal; compare with tolerance.
    if lin_avg is not None:
        chk("data-lineage desc 'avg N.NN over the 50 scored'",
            True, True, "docs/data-lineage.md",
            ok=abs(lin_avg - avg_rounded) <= 0.05)

    # --- data-lineage.md present-tense summary (broadened-DB figures) ---
    # The Source Layer "the broadened-DB figures are:" sentence and the Scoring
    # Layer section describe the CURRENT dataset; the historical "Figure
    # cascade" entries deliberately retain older numbers. These checks anchor on
    # present-tense phrasing, so a stale cascade entry can't satisfy them.
    dist = score_distribution(
        r[0] for r in conn.execute(
            "SELECT execution_score FROM projects WHERE evidence_complete = 1"))
    dist_str = "/".join(str(dist[label]) for label, _ in SCORE_BUCKETS)
    priv_avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects "
        "WHERE evidence_complete = 1 AND sector != 'Government'").fetchone()[0]

    def comma_float(s):
        return float(s.replace(",", "."))

    # Source Layer current-state summary sentence (avg 1dp, count, distribution,
    # gov/private). F5: the scored count and the gov/private averages are captured
    # as groups and compared to the DB (n_scored / gov_avg / priv_avg) rather than
    # baked in as "103" / "54,0", so the check holds at any dataset size.
    broad = re.search(
        r"the broadened-DB figures are:.*?avg (\d+,\d+) over the (\d+) scored "
        r"\(rounded \d+\), distribution (\d+/\d+/\d+/\d+/\d+), "
        r"gov/private (\d+,\d+) vs (\d+,\d+)", lineage)
    if broad:
        b_avg, b_n, b_dist, b_gov, b_priv = (
            comma_float(broad.group(1)), int(broad.group(2)), broad.group(3),
            comma_float(broad.group(4)), comma_float(broad.group(5)))
        chk("data-lineage 'broadened-DB avg (1dp)'", round(avg, 1), b_avg,
            "docs/data-lineage.md", ok=abs(b_avg - round(avg, 1)) <= 0.051)
        chk("data-lineage 'broadened-DB scored count'", n_scored, b_n,
            "docs/data-lineage.md")
        chk("data-lineage 'broadened-DB distribution'", dist_str, b_dist,
            "docs/data-lineage.md")
        chk("data-lineage 'broadened-DB gov/private gov_avg'",
            round(gov_avg, 1), b_gov, "docs/data-lineage.md",
            ok=abs(b_gov - round(gov_avg, 1)) <= 0.051)
        chk("data-lineage 'broadened-DB gov/private priv_avg'",
            round(priv_avg, 1), b_priv, "docs/data-lineage.md",
            ok=abs(b_priv - round(priv_avg, 1)) <= 0.051)
    else:
        chk("data-lineage 'broadened-DB summary sentence'", None, None,
            "docs/data-lineage.md")

    # Scoring Layer: bold average over NN scored (2dp, dot decimal). F5: the
    # scored count is captured and compared to n_scored, not hardcoded as 103.
    sl = re.search(
        r"Average score: \*\*(\d+\.\d+) over (\d+) scored projects\*\*", lineage)
    if sl:
        sl_avg, sl_n = float(sl.group(1)), int(sl.group(2))
        chk("data-lineage 'Average score NN.NN over NN scored'",
            round(avg, 2), sl_avg, "docs/data-lineage.md",
            ok=abs(sl_avg - round(avg, 2)) <= 0.005)
        chk("data-lineage 'Average score scored count'", n_scored, sl_n,
            "docs/data-lineage.md")
    else:
        chk("data-lineage 'Average score NN.NN over NN scored'", None, None,
            "docs/data-lineage.md")

    # Scoring Layer distribution table (rows in SCORE_BUCKETS order).
    tbl = dict(re.findall(r"^\| (\d+-\d+) \| (\d+) \|", lineage, re.M))
    for label, _ in SCORE_BUCKETS:
        n = int(tbl[label]) if label in tbl else None
        chk(f"data-lineage distribution table '{label}'",
            dist[label], n, "docs/data-lineage.md")

    # Derived statistics table (present-tense): "Average score (FILDA-only 43 /
    # broadened 43)" row — both are the rounded current averages.
    ds = re.search(r"\| Average score \(FILDA-only (\d+) / broadened (\d+)\) \|",
                   lineage)
    if ds:
        chk("data-lineage 'Derived stats avg FILDA-only'", round(filda_avg),
            int(ds.group(1)), "docs/data-lineage.md")
        chk("data-lineage 'Derived stats avg broadened'", round(avg),
            int(ds.group(2)), "docs/data-lineage.md")
    else:
        chk("data-lineage 'Derived stats avg row'", None, None, "docs/data-lineage.md")

    # Article Layer: Gov vs private row. F5: both the broadened gov and private
    # averages (and the FILDA-only pair) are captured as groups and compared to
    # the DB, not hardcoded as "54,0".
    filda_priv_avg = conn.execute(
        "SELECT AVG(execution_score) FROM projects "
        "WHERE evidence_complete = 1 AND source_program = 'FILDA' "
        "AND sector != 'Government'").fetchone()[0]
    gp = re.search(
        r"\| Gov vs private \((\d+,\d+) vs (\d+,\d+) broadened; "
        r"FILDA-only (\d+,\d+) vs (\d+,\d+)\)", lineage)
    if gp:
        gp_gov, gp_priv = comma_float(gp.group(1)), comma_float(gp.group(2))
        gp_fgov, gp_fpriv = comma_float(gp.group(3)), comma_float(gp.group(4))
        chk("data-lineage 'Gov vs private broadened gov_avg'",
            round(gov_avg, 1), gp_gov, "docs/data-lineage.md",
            ok=abs(gp_gov - round(gov_avg, 1)) <= 0.051)
        chk("data-lineage 'Gov vs private broadened priv_avg'",
            round(priv_avg, 1), gp_priv, "docs/data-lineage.md",
            ok=abs(gp_priv - round(priv_avg, 1)) <= 0.051)
        chk("data-lineage 'Gov vs private FILDA-only gov_avg'",
            round(gov_avg, 1), gp_fgov, "docs/data-lineage.md",
            ok=abs(gp_fgov - round(gov_avg, 1)) <= 0.051)
        chk("data-lineage 'Gov vs private FILDA-only priv_avg'",
            round(filda_priv_avg, 1), gp_fpriv, "docs/data-lineage.md",
            ok=abs(gp_fpriv - round(filda_priv_avg, 1)) <= 0.051)
    else:
        chk("data-lineage 'Gov vs private row'", None, None,
            "docs/data-lineage.md")

    # Article Layer: edition averages (Limitations #5 prose, present-tense).
    # Compare the doc's 1-dp value to the RAW average with a tolerance that
    # accepts either nearest rounding (a .5 boundary like 43.25 is valid as 43.2
    # or 43.3 — convention, not drift); a real drift >= 0.15 still fails.
    ed = re.search(r"rises by edition \(2022: ([\d.]+) → 2026: ([\d.]+)\)", lineage)
    if ed:
        e22, e26 = float(ed.group(1)), float(ed.group(2))
        chk("data-lineage edition avg 2022",
            round(ed22, 1) if ed22 is not None else None, e22,
            "docs/data-lineage.md",
            ok=(ed22 is not None and abs(e22 - ed22) <= 0.051))
        chk("data-lineage edition avg 2026",
            round(ed26, 1) if ed26 is not None else None, e26,
            "docs/data-lineage.md",
            ok=(ed26 is not None and abs(e26 - ed26) <= 0.051))
    else:
        chk("data-lineage edition avg 2022", None, None, "docs/data-lineage.md")
        chk("data-lineage edition avg 2026", None, None, "docs/data-lineage.md")

    # Article Layer: Investment Portal case-study score.
    ip_m = re.search(r"Investment Portal is also DB-tracked at score (\d+)", lineage)
    if ip_m:
        chk("data-lineage 'Investment Portal score'", ip_score, int(ip_m.group(1)),
            "docs/data-lineage.md")
    else:
        chk("data-lineage 'Investment Portal score'", None, None,
            "docs/data-lineage.md")

    # Article Layer: broadened distribution in the claim-traceability table.
    ad = re.search(
        r"Distribution table \d+/\d+/\d+/\d+/\d+ \(FILDA-only\) / "
        r"(\d+/\d+/\d+/\d+/\d+) \(broadened\)", lineage)
    if ad:
        chk("data-lineage 'Article-Layer broadened distribution'",
            dist_str, ad.group(1), "docs/data-lineage.md")
    else:
        chk("data-lineage 'Article-Layer broadened distribution'", None, None,
            "docs/data-lineage.md")

    # Article Layer: six FILDA-only sector averages in the Sector-table row.
    sec = re.search(
        r"Agriculture ([\d,]+); Energy ([\d,]+); Manufacturing ([\d,]+); "
        r"Infrastructure ([\d,]+); Technology ([\d,]+); Multi-sector ([\d,]+)",
        lineage)
    if sec:
        for name, g in (("Agriculture", 1), ("Energy", 2), ("Manufacturing", 3),
                        ("Infrastructure", 4), ("Technology", 5),
                        ("Multi-sector", 6)):
            doc_val = comma_float(sec.group(g))
            db_val = filda_sector.get(name)
            chk(f"data-lineage 'Article-Layer sector avg {name}'",
                round(db_val, 1) if db_val is not None else None, doc_val,
                "docs/data-lineage.md",
                ok=(db_val is not None and abs(doc_val - db_val) <= 0.051))
    else:
        for name in ("Agriculture", "Energy", "Manufacturing", "Infrastructure",
                     "Technology", "Multi-sector"):
            chk(f"data-lineage 'Article-Layer sector avg {name}'", None, None,
                "docs/data-lineage.md")

    # --- scoring-methodology.md worked examples ---
    # Each "### Title (`project-id`)" heading is followed by a
    # "**Score: ... = NN**" line. Compare NN to the DB execution_score for that id.
    scoring = read(SCORING)

    # scoring-methodology.md Limitations #5 also cites the edition averages.
    # Same rounding-agnostic comparison as the data-lineage edition-avg checks.
    sed = re.search(
        r"rises steadily by edition \(2022: ([\d.]+) → 2026: ([\d.]+)\)", scoring)
    if sed:
        se22, se26 = float(sed.group(1)), float(sed.group(2))
        chk("scoring-methodology edition avg 2022",
            round(ed22, 1) if ed22 is not None else None, se22,
            "docs/scoring-methodology.md",
            ok=(ed22 is not None and abs(se22 - ed22) <= 0.051))
        chk("scoring-methodology edition avg 2026",
            round(ed26, 1) if ed26 is not None else None, se26,
            "docs/scoring-methodology.md",
            ok=(ed26 is not None and abs(se26 - ed26) <= 0.051))
    else:
        chk("scoring-methodology edition avg 2022", None, None,
            "docs/scoring-methodology.md")
        chk("scoring-methodology edition avg 2026", None, None,
            "docs/scoring-methodology.md")

    lines = scoring.splitlines()
    i = 0
    while i < len(lines):
        hm = re.match(r"### .+\(`([\w-]+)`\)", lines[i])
        if hm:
            pid = hm.group(1)
            # Find the next "**Score: ... = NN**" within the next ~12 lines.
            for j in range(i + 1, min(i + 13, len(lines))):
                sm = re.search(r"Score:.*?=\s*(\d+)\*\*", lines[j])
                if sm:
                    db_score = conn.execute(
                        "SELECT execution_score FROM projects WHERE id = ?",
                        (pid,)).fetchone()
                    expected = db_score[0] if db_score else None
                    chk(f"scoring-methodology worked example '{pid}'",
                        expected, int(sm.group(1)),
                        f"docs/scoring-methodology.md:{j+1}")
                    i = j
                    break
        i += 1

    return checks


def main():
    if not os.path.exists(DB_path):
        sys.exit(f"Database not found at {DB_path}. Run `python db/load.py` first.")

    conn = sqlite3.connect(f"file:{DB_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    checks = run_checks(conn)
    conn.close()

    # --- Report ---
    width = 56
    print(f"{'Check':<{width}}{'Result':>8}")
    print("-" * (width + 8))
    failures = 0
    for label, ok, expected, actual, location in checks:
        print(f"{label[:width]:<{width}}{'PASS' if ok else 'FAIL':>8}")
        if not ok:
            failures += 1
            print(f"    {location}: expected {expected!r}, doc says {actual!r}")
    print("-" * (width + 8))
    print(f"{len(checks)} checks, {len(checks) - failures} passed, {failures} failed")
    if failures:
        print("\n[FAIL] A doc cites a number the DB contradicts — update the doc, or fix the data.")
        sys.exit(1)
    print("\n[OK] Cited doc figures match the DB.")


if __name__ == "__main__":
    main()
