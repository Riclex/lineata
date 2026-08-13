-- Angola Investment Execution Database
-- SQLite schema
-- Created: 2026-07-23

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- Projects
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    sector              TEXT,
    subsector           TEXT,
    description         TEXT,
    country             TEXT DEFAULT 'Angola',
    province            TEXT,
    municipality        TEXT,
    coordinates          TEXT,  -- "lat, lon"
    status              TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (status IN (
                            'announced', 'mou_signed', 'financed',
                            'under_construction', 'delayed', 'suspended',
                            'restarted', 'operational', 'completed',
                            'cancelled', 'unknown'
                        )),
    announced_value     REAL,
    currency            TEXT DEFAULT 'USD',
    estimated_jobs      INTEGER,
    expected_completion TEXT,  -- YYYY or YYYY-MM
    actual_completion   TEXT,  -- YYYY or YYYY-MM
    execution_score     INTEGER DEFAULT 0
                        CHECK (execution_score >= 0 AND execution_score <= 100),
    filda_edition       TEXT,  -- e.g. "2023"; NULL for non-FILDA projects (Tier 3)
    source_program      TEXT NOT NULL DEFAULT 'FILDA',  -- FILDA / AIPEX / refinery / PPP / multilateral (see constants.SOURCE_PROGRAMS); Tier 3 broadens coverage beyond FILDA
    last_verified       TEXT,  -- YYYY-MM-DD a human last checked this project's status against sources
    evidence_complete   INTEGER DEFAULT 1,  -- 0 = tracked but NOT scored (no click-through evidence; see data-lineage.md "Event 80")
    is_externally_blocked INTEGER DEFAULT 0,  -- label only (Gap 2): 1 = stalled by an external force (judicial/regulatory/disbursement), not underperformance; does NOT affect the score
    data_completeness   TEXT,  -- announcement_only | partial | full — how much of the timeline is recorded; derived from events by load.py (see constants.data_completeness), drift-checked by verify_invariants.py
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_projects_sector    ON projects(sector);
CREATE INDEX IF NOT EXISTS idx_projects_province  ON projects(province);
CREATE INDEX IF NOT EXISTS idx_projects_status    ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_filda    ON projects(filda_edition);
CREATE INDEX IF NOT EXISTS idx_projects_program ON projects(source_program);

-- ============================================================
-- Organizations
-- ============================================================
CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL
                CHECK (type IN (
                    'company', 'government', 'state_owned_enterprise',
                    'foreign_investor', 'contractor', 'financier'
                )),
    country     TEXT,
    parent_org_id TEXT REFERENCES organizations(id),
    aliases      TEXT,    -- JSON array: ["Alt Name 1", "Alt Name 2"]
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_org_type    ON organizations(type);
CREATE INDEX IF NOT EXISTS idx_org_country ON organizations(country);
CREATE INDEX IF NOT EXISTS idx_org_parent   ON organizations(parent_org_id);

-- ============================================================
-- Project ↔ Organization (many-to-many with role)
-- ============================================================
CREATE TABLE IF NOT EXISTS project_organizations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL
                    CHECK (role IN (
                        'promoter', 'investor', 'contractor',
                        'financier', 'partner', 'operator'
                    )),
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, organization_id, role)
);

CREATE INDEX IF NOT EXISTS idx_po_project ON project_organizations(project_id);
CREATE INDEX IF NOT EXISTS idx_po_org      ON project_organizations(organization_id);

-- ============================================================
-- Events (project timelines)
-- ============================================================
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL
                CHECK (event_type IN (
                    'announcement', 'mou', 'financing', 'groundbreaking',
                    'construction', 'delay', 'suspension', 'restart',
                    'completion', 'expansion', 'closure', 'ownership_change'
                )),
    event_date  TEXT,  -- YYYY-MM-DD, YYYY-MM, or YYYY
    description TEXT,
    source_id   INTEGER REFERENCES sources(id),
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_events_project   ON events(project_id);
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_date      ON events(event_date);

-- ============================================================
-- Project evidence (field-level provenance)
-- ============================================================
-- The goal: "don't score projects unless someone can click through the
-- evidence." events.source_id links a timeline event to a source, but a
-- project's own fields (status, announced_value, estimated_jobs,
-- actual_completion) also need a click-through source. This table records,
-- per project field, the value as observed and the source that backs it.
CREATE TABLE IF NOT EXISTS project_evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    field        TEXT NOT NULL,   -- which project field this evidence backs (status, announced_value, estimated_jobs, actual_completion, ...)
    value        TEXT,            -- the value as observed in the source
    source_id    INTEGER REFERENCES sources(id),
    observed_at  TEXT,            -- YYYY-MM-DD the value was observed
    created_at   TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, field, source_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_project ON project_evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_evidence_field   ON project_evidence(field);

-- ============================================================
-- Sources
-- ============================================================
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT,
    url          TEXT,
    date         TEXT,  -- publication date
    publisher    TEXT,
    archived_url TEXT,  -- web.archive.org / archive.today link
    confidence   TEXT DEFAULT 'medium'
                 CHECK (confidence IN ('high', 'medium', 'low')),
    last_verified TEXT,  -- YYYY-MM-DD the URL was last checked to resolve (see db/verify_sources.py)
    url_status   TEXT,   -- 'alive' | 'dead' | 'blocked' | 'n/a' (empty URL / publisher-only)
    created_at   TEXT DEFAULT (datetime('now'))
);

-- One URL = one source. A partial unique index (skips empty-URL / publisher-only
-- rows, which legitimately repeat as '') makes db/update.py's resolve_or_create_source
-- dedup race-free and forbids accidental duplicate URLs from CSV edits or concurrent
-- inserts. Enforced at the DB level so the constraint survives every rebuild.
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_url ON sources(url) WHERE url != '';

-- ============================================================
-- Views: Aggregated execution metrics
--
-- All views filter to evidence_complete = 1 (SCORED projects only). An
-- unscored project carries execution_score = 0 by convention, not a measured
-- zero; including it would drag averages down and inflate totals with projects
-- we don't evidence-track. See docs/data-lineage.md "Event 80".
-- ============================================================

-- Execution score by organization (promoter/investor)
CREATE VIEW IF NOT EXISTS v_execution_by_org AS
SELECT
    o.name AS organization,
    o.type AS org_type,
    o.country AS org_country,
    COUNT(DISTINCT po.project_id) AS total_projects,
    COUNT(DISTINCT CASE WHEN p.status = 'completed' THEN po.project_id END) AS completed,
    COUNT(DISTINCT CASE WHEN p.status = 'cancelled' THEN po.project_id END) AS cancelled,
    COUNT(DISTINCT CASE WHEN p.status IN ('delayed','suspended') THEN po.project_id END) AS delayed,
    ROUND(AVG(p.execution_score), 1) AS avg_execution_score
FROM organizations o
JOIN project_organizations po ON po.organization_id = o.id
JOIN projects p ON p.id = po.project_id
WHERE p.evidence_complete = 1
GROUP BY o.id;

-- Execution by sector
CREATE VIEW IF NOT EXISTS v_execution_by_sector AS
SELECT
    sector,
    COUNT(*) AS total_projects,
    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
    SUM(CASE WHEN status IN ('delayed','suspended') THEN 1 ELSE 0 END) AS delayed,
    ROUND(100.0 * SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) / COUNT(*), 1) AS completion_rate
FROM projects
WHERE sector IS NOT NULL AND evidence_complete = 1
GROUP BY sector;

-- Execution by province
CREATE VIEW IF NOT EXISTS v_execution_by_province AS
SELECT
    province,
    COUNT(*) AS total_projects,
    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
    SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
    SUM(CASE WHEN status IN ('delayed','suspended') THEN 1 ELSE 0 END) AS delayed,
    ROUND(100.0 * SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) / COUNT(*), 1) AS completion_rate
FROM projects
WHERE province IS NOT NULL AND evidence_complete = 1
GROUP BY province;

-- Execution by investor country
CREATE VIEW IF NOT EXISTS v_execution_by_country AS
SELECT
    o.country AS investor_country,
    COUNT(DISTINCT po.project_id) AS total_projects,
    COUNT(DISTINCT CASE WHEN p.status = 'completed' THEN po.project_id END) AS completed,
    COUNT(DISTINCT CASE WHEN p.status = 'cancelled' THEN po.project_id END) AS cancelled
FROM organizations o
JOIN project_organizations po ON po.organization_id = o.id
JOIN projects p ON p.id = po.project_id
WHERE o.country IS NOT NULL AND p.evidence_complete = 1
GROUP BY o.country;

-- ============================================================
-- Triggers: auto-update updated_at
-- ============================================================
-- The WHEN guard fires the trigger only when updated_at is NOT itself being
-- changed (OLD.updated_at IS NEW.updated_at), so the trigger's own UPDATE of
-- updated_at does not re-fire it. Without this guard the trigger recursed on
-- its own write and only avoided a stack overflow because SQLite's
-- recursive_triggers pragma defaults to OFF -- a latent bug if anyone ever
-- enables it. The guard is column-list-independent, so it survives schema
-- changes without enumerating every non-updated_at column.
CREATE TRIGGER IF NOT EXISTS trg_projects_updated
AFTER UPDATE ON projects
FOR EACH ROW
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE projects SET updated_at = datetime('now') WHERE id = OLD.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_organizations_updated
AFTER UPDATE ON organizations
FOR EACH ROW
WHEN OLD.updated_at IS NEW.updated_at
BEGIN
    UPDATE organizations SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- ============================================================
-- Change log (append-only audit trail)
-- ============================================================
-- The goal's #1 strategic challenge: "make the data model event-driven so
-- updates are incremental instead of periodic manual reviews." Every
-- mutation made through db/update.py writes one row here — "store every
-- observation." Loaded from data/change_log.csv by load.py (so the audit
-- trail survives rebuilds and is git-diffable), NOT exempt from the rebuild.
CREATE TABLE IF NOT EXISTS change_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL DEFAULT (datetime('now')),
    operation     TEXT NOT NULL,   -- 'add-source'|'add-event'|'add-evidence'|'set-status'|'relink-event'|'relink-evidence'|'reverify'|'retype-event'|'export-csv'|'load-seed'
    target_table  TEXT NOT NULL,   -- 'sources'|'events'|'project_evidence'|'projects'|'db_meta'
    target_id     TEXT,            -- row id of the target (TEXT covers both TEXT and INTEGER PKs)
    payload_json  TEXT,            -- JSON snapshot of what was written/changed
    source_url    TEXT,            -- the --source-url that authorized the change (NULL for reverify/export/load)
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_changelog_ts     ON change_log(ts);
CREATE INDEX IF NOT EXISTS idx_changelog_target ON change_log(target_table, target_id);
CREATE INDEX IF NOT EXISTS idx_changelog_op     ON change_log(operation);

-- ============================================================
-- DB metadata (checkpoint watermark)
-- ============================================================
-- Holds the 'last_exported_at' watermark used by load.py's staleness guard:
-- if change_log has MUTATION rows (MUTATION_OPS: add-*/set-status/relink-*/
-- reverify — see db/constants.py) newer than last_exported_at, load.py refuses
-- to rebuild (it would silently lose uncheckpointed DB edits) unless --force
-- is passed. Updated by db/export_csv.py on every successful checkpoint.
CREATE TABLE IF NOT EXISTS db_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);