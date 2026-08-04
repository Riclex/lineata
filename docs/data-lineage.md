# Data Lineage — Angola Investment Execution Database

## Overview

This document traces every data point in the database from its origin to its final form. It maps the flow: raw source → research file → CSV → SQLite → computed score → article claim.

---

## Data Pipeline

```
RAW SOURCES (web articles, press releases, official sites)
    ↓
RESEARCH FILES (research/*.md — manual + subagent collection)
    ↓
CSV FILES (data/*.csv — structured extraction)
    ↓
SQLITE DATABASE (db/investment_tracker.db — loaded via db/load.py)
    ↓
COMPUTED SCORES (db/calculate_scores.py — formula applied)
    ↓
PUBLISHED CLAIMS (cited back to sources)
```

---

## Source Layer: Where the Data Comes From

### 139 sources in the database, each with a confidence level

The sources table was rebuilt on 2026-07-25 from the research files, then extended with a targeted web-research pass. It retains the 15 original hand-curated sources (the duplicate `id 11`, which shared source 15's URL, was collapsed into 15), adds 105 sources extracted from the research files — VerAngola, SCM/Ministry of Industry, Forbes Portugal/África Lusófona, Jornal de Angola, Angolan Mining Oil & Gas, RFI, BAI Europa, Sonangol official, AEP, ApexBrasil, Portugal.gov.pt, CIPRA, CIP, Embaixada de Portugal, INACOM, GGPEN, and many more — adds 8 more (ids 122–129) found by direct web search to ground the previously-NULL events, adds 1 (id 130, the VerAngola Jan-2026 Huatong inauguration article) on 2026-07-30 to ground event 30, and adds 1 (id 131, the OPaís 13 Apr 2026 first-export article) on 2026-07-30 to ground event 105 (Huatong's first 1,000-ton aluminum export to the Netherlands). The previous "11+ sources used in research but not entered" gap is closed.

| Confidence | Sources | Notes |
|-----------|---------|-------|
| high | ~50 | official outlets, government portals, primary org sites (Sonangol, ANPG, BFA, AIPEX, CIPRA, CIP, Portugal.gov.pt, INACOM, GGPEN, BAI Europa, AEP, ApexBrasil, ETU Energias, Portugal Global/AICEP, Angop) |
| medium | ~73 | reputable secondary press (VerAngola, RFI, Jornal de Negócios, Diário de Notícias, Expansão, Angop, ECO, DN, Rádio Eclesia, Forbes, 360 Angola, Correio da Kianda, Medafrica Times) |
| low | 5 | weak/aggregate citations, including 3 publisher-only records with no pinned article URL (see below) |

### Event → source linking state (after re-linking + NULL grounding)

| State | Events | Detail |
|-------|--------|--------|
| Linked to a specific source | 103 | grounded to a full-URL article or a named publisher |
| `source_id` NULL | 1 | event 80 (Banco Sol Cartão Multicaixa Empresas) — no grounded FILDA-2025 launch article |

The single remaining NULL event (80) is an honest gap: the research and a web search surfaced only Banco Sol's year-round product pages, not a FILDA-2025 launch article, so the source is left unpinned rather than fabricated. (Event 30, briefly NULL during the 2026-07-30 Huatong fix, was grounded same-day to source 130 — see "Huatong source mis-link corrected 2026-07-30".)

### Publisher-only sources (no pinned article URL)

Three sources carry a named publisher but an empty `url` — the research file attributes the claim to that publisher without giving a full article URL. They exist so the attribution is recorded honestly rather than NULLed:

| ID | Publisher | Linked events | Claim |
|----|-----------|---------------|-------|
| 119 | Jornal de Negócios | 26 | Sonangol profits fell 11% in 2025 |
| 120 | Reuters | 27 | Sonangol seeking $4.8B China loan (Feb 2026) |
| 121 | Angolan Mining Oil & Gas | 28, 29 | ANPG Block 33/24 dev agreement; $100B pipeline / Q1 2026 revenue |

**Source concentration risk (resolved):** Before re-linking, source 15 (a single generic CIPRA article) was attached to 80 of 104 events (77%) — a fabrication risk, since most of those events were not actually about that article. Re-linking retired that: source 15 now backs exactly 1 event (event 16, the Huatong completion, which *is* about that article). No single source dominates the event timeline; the largest is source 54 (the VerAngola AIPEX-Awards article) at 15 events, all genuinely covered by that article. (A second generic-link survivor, source 16 — a 2023 Menos Fios opening article that had been attached to Huatong's 2026 events — was retired 2026-07-30; it now backs 0 events. See "Huatong source mis-link corrected 2026-07-30".)

---

## Research Layer: Collection Method

### How each edition was collected

| Edition | Collector | Method | File | Projects Found | Iterations |
|---------|-----------|--------|------|-----------------|------------|
| 2022 | Subagent | Ecosia + browser | filda-2022-raw.md | 13 entries | 39 API calls |
| 2023 | Subagent + manual | Browser + console extraction | filda-2023-raw.md | 14 entries | 50 API calls + manual |
| 2024 | Subagent | Ecosia + browser | filda-2024-raw.md | 24 entries | 50 API calls |
| 2025 | Subagent (incomplete) | Ecosia + browser | filda-2025-raw.md (written from transcript) | 12 entries | 50 API calls, hit limit |
| 2026 | Subagent | VerAngola internal search | filda-2026-raw.md | 10 entries | 46 API calls |

### Status trace collection

| Topic | Collector | File | Key Finding |
|-------|-----------|------|-------------|
| Government announcements | Subagent | status-trace-gov.md | Linha Verde never launched; portal launched |
| Company announcements | Subagent (incomplete) | status-trace-companies.md (written from summary) | Huatong expanded massively; ETU IPO-bound |
| Trade/investment | Subagent | status-trace-trade.md | FILDA renamed; Brazil continued; EUR 2B was misquote |
| Participation analysis | Subagent | filda-participation-analysis.md | Numbers are direct+indirect, not unique companies |

---

## Transformation Layer: CSV → Database

### Data quality issues found during analysis

1. **CSV quoting:** Descriptions containing commas broke early CSV parsing. Fixed by using Python's csv module with proper quoting in later loads.

2. **FK constraint on organizations:** parent_org_id references organizations that may not be loaded yet. Fixed by disabling FK during load, re-enabling after.

3. **Execution score parsing:** Empty string in execution_score column caused ValueError. Fixed by checking for empty strings before int conversion.

4. **Currency confusion:** The "EUR 2 billion" FILDA 2023 figure was likely "2 billion Kwanzas" (~$4M USD), not EUR. This was discovered during status tracing and noted in the research files.

5. **Participation count misinterpretation:** Numbers reported as "companies" or "expositores" are actually total participations (direct + indirect). Corrected in the published article and research files.

6. **2025 figure discrepancies:** The 2,194 figure used in initial research was not found in any source. Actual figures: "mais de 2,200" (RFI), 2,044 (MinCom), "cerca de 1,800" (Economia & Mercado). Corrected in research file.

7. **87% Angolan figure:** Not found in any source. The confirmed figure is "cerca de 80%" from RFI quoting Albernaz. Corrected in research file.

---

## Database Layer: Current State

### Counts

| Table | Records | Notes |
|-------|---------|-------|
| projects | 57 | 5 FILDA editions (2022-2026) + Tier 3: 5 AIPEX-promoted + 1 refinery build |
| organizations | 69 | companies, government, foreign investors, SOEs |
| events | 115 | All 57 projects have at least one event |
| sources | 139 | 105 from research files + 8 from targeted web research + 1 (id 130) Huatong Jan-2026 inauguration + 1 (id 131) Huatong Apr-2026 first export + 1 (id 132) AOG-2025 Block 33/24 dev agreement + 1 (id 133) ANPG $100B pipeline + 1 (id 134) Angola Startup Summit III edition winners + 1 (id 135) Portugal.gov.pt 3.25B credit line + 1 (id 136) AIPEX May-2024 six-investment-contracts signing + 1 (id 137) Quilemba Solar construction start (BusinessWire) + 1 (id 138) Baia Fish inauguration (VerAngola) + 1 (id 139) Cabinda refinery financing (Africa Oil & Gas Report) + 1 (id 140) Cabinda refinery inauguration (Angola Petroleum); 114 events linked to a specific source, 1 NULL (see Source Layer) |
| project_organizations | 91 | 56 of 57 projects have org links (hospital-serum-factory pending — no confirmed investor in the AIPEX announcement) |

### Coverage — previously a critical gap, now resolved

The previous version of this document flagged that 37 of 51 projects (73%) had no events and 37 of 51 had no organization links. **This is now resolved:** all 51 projects have events and all 51 have organization links.

| Edition | Total Projects | With Events | With Org Links |
|---------|---------------|-------------|----------------|
| 2022 | 11 | 11 (100%) | 11 (100%) |
| 2023 | 14 | 14 (100%) | 14 (100%) |
| 2024 | 11 | 11 (100%) | 11 (100%) |
| 2025 | 7 | 7 (100%) | 7 (100%) |
| 2026 | 8 | 8 (100%) | 8 (100%) |

### Data integrity fixes applied 2026-07-25

While building the reproducible loader (`db/load.py`), `PRAGMA foreign_key_check` exposed two latent CSV bugs that had been hidden because nothing previously enforced foreign keys:

1. **Merged row in `events.csv`** — events 38 and 39 had lost the newline between them and were stored on one physical line (11 fields instead of 6). Event 39 (the `grain-production-ambition-2022` announcement, source 15) was being silently dropped on every load. Fixed by splitting back into two rows; event count went 103 → 104.
2. **Bogus `source_id`** — event 38 had `source_id=1539`, but only 16 sources exist. Set to NULL (the event is a data-correction note with no single source).
3. **Dangling `parent_org_id`** — `bai-europa` referenced parent `bai`, which is not in `organizations.csv`. Set to NULL rather than fabricating an unsourced parent org. The BAI parent entity should be added later with a source.

### Governance hardening applied 2026-07-25

Four controls were added so the integrity checks that were once run manually now run on every rebuild, and the published figures are pinned to the DB by a regression net:

1. **`foreign_key_check` is now an automated gate in `db/load.py`.** After the bulk insert and FK re-enable, the loader runs `PRAGMA foreign_key_check` and fails the load (non-zero exit, violations printed) if any row references a missing parent. The CSV bugs above are the class of defect this now catches on every rebuild, not just once.
2. **Score-consistency gate in `db/load.py`.** `execution_score` is loaded from `projects.csv` (a snapshot), then recomputed from the loaded data via `calculate_scores.compute_scores` and asserted equal. A stale snapshot fails the load with a per-project diff. This caught a real, broad staleness on first run — 37 score cells in `projects.csv` had drifted from the formula (the CSV had never been refreshed after past `calculate_scores.py` runs) — and they were synced (see #3). The DB now ships only formula-verified scores.
3. **`db/calculate_scores.py --update-csv`** recomputes scores and rewrites the `execution_score` column in `data/projects.csv`, so the snapshot can be refreshed in one command after any data edit (instead of drifting silently).
4. **`db/verify_invariants.py`** (32 structural checks) + **`db/verify_snapshot.py`** (auto-generated snapshot + article pin) replaced the old `db/verify.py`. The snapshot is committed as `db/snapshot.json` and regenerated with `--update`. After the v2-2026-07 formula change, 17 operational-without-progress downgrades (2026-08-03), the Tier 1 dataset cleanup (2026-08-04), and the Tier 3 AIPEX cohort + source_program schema (2026-08-04), the published figures are: 57 tracked / 56 scored, 139 sources / 115 events (114 linked / 1 NULL), avg 42,2 over the 56 scored, distribution 19/14/2/12/9, gov/private 54,0 vs 41,5. Exits non-zero on any drift.

The scoring methodology docs were also reconciled to the code: `docs/scoring-methodology.md` now documents the *actual* evidence-bonus rule (and notes that the previously documented `sources.confidence` weighting is **not** implemented), its worked examples reproduce against the script (83 / 3 / 83), and the stale formula in `docs/data-model.md` was replaced with a pointer to the methodology doc.

Reproduce with:
```
python db/load.py                 # rebuild + foreign_key_check + score-consistency gate
python db/calculate_scores.py     # score report (avg 60.68 over 50 scored, 10/1/7/20/12)
python db/verify_invariants.py    # structural invariants (FK, score_version, change_log orphans; 29 checks)
python db/verify_snapshot.py       # article↔DB contract: pins published figures to db/snapshot.json + the published articles
python db/query.py --summary      # read-only JSON access layer (workflow-integration leg)
```

### Alignment work applied 2026-07-25

A targeted pass to align the project with its stated goal — "build the authoritative historical record of announced investments and their execution, where every claim is click-through-able to evidence." Four foundational controls plus the field-level-provenance starter:

**1. `created_at` now persists across rebuilds.** `db/load.py` recreates the DB fresh each run (deleting the file first, since `CREATE TABLE IF NOT EXISTS` won't add columns to an existing table) and now loads `created_at` from `projects.csv` / `organizations.csv`. Previously every rebuild reset all `created_at` to "now," destroying first-seen history — the opposite of the goal's "don't overwrite, store every observation." Existing rows are backfilled with `2026-07-23` (the schema/DB origin date) as an honest *dataset-established* date, not a per-row first-seen (those were never recorded). `updated_at` still refreshes per rebuild — a known limitation (it reflects last reload, not last edit), flagged for future work.

**2. `last_verified` + source URL liveness.** Added `projects.last_verified` (hand field, backfilled `2026-07-25` — the last full reconciliation), and `sources.last_verified` + `sources.url_status` (auto-stamped). `db/verify_sources.py` HEAD/GET-checks every source URL, classifies `alive` / `blocked` / `dead` / `n/a`, and writes the result back to `sources.csv` + the DB (so it survives rebuilds). This operationalises the goal's "latest verification" field and the risk it names as largest ("continuous verification is expensive").

  Result of the first full pass (2026-07-25): **114 alive, 4 blocked, 7 dead, 3 n/a**. The 7 dead and 4 blocked links are real link-rot — the goal's remedy is the existing `archived_url` field (web.archive.org / archive.today); they are flagged here, not silently fixed, per the no-fabrication discipline:
  - Dead: sources 12 (RFI — URL has mojibake-corrupted characters), 18 (SA Gov / dtic), 64 (Notícias ao Minuto), 79 (telecom.na), 104 (ApexBrasil), 110 (startupsummit.gov.ao), 115 (PortugalGlobal).
  - Blocked (401/403): 6 (AllAfrica/ANGOP), 29 (RFI — same article as 12 but clean URL), 83 (Embaixada de Portugal), 114 (RFI).
  - Note: sources 12 and 29 are the same RFI article; 12's URL is corrupted and 29's is clean-but-blocked — a dedup candidate.

**3. `project_evidence` table (field-level provenance).** Table `(project_id, field, value, source_id, observed_at)` so a project's own fields (`status`, `announced_value`, `estimated_jobs`, `actual_completion`) are click-through-able to a source, not just its events. The 7 published case-study projects were hand-curated (14 rows); `db/_extract/expand_evidence.py` then applied the same convention mechanically to the remaining projects (50 generated rows → **64 rows total, 48/50 scored projects covered**). **Convention (documented, not overclaimed):** `source_id` is the source behind the *event that establishes the field*, and `observed_at` is that event's date — e.g. Huatong's `announced_value=900000000` → source 16 (the $900M-protocol article), observed 2026-07-20. Fields with no sourced establishing event are **skipped** (left unprovenanced) rather than linked to an unrelated source — no fabrication. The 2 scored projects still without field evidence are `banco-sol-virtual-assistant` (status `unknown`, nothing to pin) and `cabinda-refinery-aipex-2026` (under_construction without a construction event — see open issue below).

**4. New `verify.py` checks (69 total as of this pass, up from 51; 77 after the 2026-07-27 incremental layer; 81 after the 2026-07-30 relink ops + export event):**
- *Status supported by an event* — every `completed`/`under_construction` project must have a progress event (completion/construction/groundbreaking/financing). Deliberately excludes `operational` (non-physical projects are legitimately operational without a build event; flagging them would be a false positive that erodes trust).
- *Case-study field-level evidence* — each published case study must have ≥1 `project_evidence` row.
- *Scored project must have a source-linked event* — operationalises the goal's "no score without click-through evidence." Surfaces 1 known open issue as a *warning* (not a hard fail, to avoid fabricating a fix): see event 80 below.

**Event 80 (Banco Sol) — resolved 2026-07-25 (unscored, not fabricated).** Event 80 (`banco-sol-mc-empresas-2025`, "Banco Sol launched Cartão Multicaixa Empresas at FILDA 2025") remained the single NULL event. A targeted web search could **not** ground it, and surfaced that the FILDA 2025 Multicaixa launch was actually **Pay4All's** é-Kwanza card (not Banco Sol's), and Banco Sol's Mastercard partnership was May 2026 — so the event appears **misattributed**, not merely unsourced. Per the no-fabrication discipline it is **not** pinned to an invented source. Resolution: a new `projects.evidence_complete` flag (default 1) marks Banco Sol `0` — *tracked but not scored*. `calculate_scores.py` returns 0 for `evidence_complete = 0` and excludes it from published averages/distributions, operationalising the goal's "don't score projects unless someone can click through the evidence." This moved the headline average from 61 (over 51) to **62 (over 50 scored)**; both articles and `verify.py` were updated to the new figures (see "Execution sweep applied 2026-07-25").

**Other data-quality observations surfaced (flagged, not auto-fixed):**
- `cabinda-refinery-aipex-2026` had `status=under_construction` but a `completion` event. Resolved 2026-07-25: the event was an **AIPEX Energy award**, not a completion — re-typed `completion` → `expansion` (the codebase's own convention for awards/recognition, see `calculate_scores.py` evidence-bonus). The `under_construction` status is plausibly true (the Cabinda refinery is a real Sonangol project) but has no construction/groundbreaking/financing source in this DB, so per the "flag, don't silently fix" discipline the status is **kept** and surfaced by `verify.py` as a known open issue (needs a construction source) rather than silently downgraded. The re-type moved its score 60 → 55 (still 41-60 bucket), lowering the Energy sector average 73,9 → 73,1.
- `organizations.csv` had a ragged row (a spurious 8th field in one org row, silently dropped by `load.py` on every past load — same class as the merged-events bug). Recovered `organizations.csv` from the DB (the faithful ingested state); the spurious field's content was never ingested and is unrecoverable. `add_columns.py` hardened with `extrasaction='ignore'` so a ragged row can't abort a future migration.

Reproduce:
```
python db/_extract/add_columns.py        # one-time: adds the new CSV columns + evidence skeleton
python db/_extract/expand_evidence.py --apply   # field-level evidence for the non-case-study projects
python db/_extract/archive_sources.py --apply    # backfill sources.archived_url from the Wayback API
python db/load.py                         # fresh rebuild + integrity gates (now loads created_at + evidence_complete)
python db/verify_sources.py --apply       # URL liveness → stamps sources.last_verified + url_status
python db/verify_invariants.py            # structural invariants (FK, score_version, change_log orphans)
python db/verify_snapshot.py              # article↔DB contract (replaced the old verify.py; pins db/snapshot.json + the published articles)
python db/query.py --summary              # read-only JSON access layer
```

### Execution sweep applied 2026-07-25

A batch closing the deferred items from the alignment pass. Five changes, each honouring the no-fabrication discipline:

1. **`archived_url` backfill (`db/_extract/archive_sources.py`).** For the 11 dead/blocked source URLs, the script queries the Wayback Machine availability API and records the closest snapshot. **4 verified snapshots** were found and written (sources 6, 18, 64, 110); the other 7 (incl. the three RFI URLs, which archive.org does not hold) have **no API-verifiable snapshot** and are left empty rather than guessed — RFI is likely on archive.today but its API rate-limits automated access, so those await manual lookup. Source 12's URL is mojibake-corrupted; the script queries using source 29's clean URL (same RFI article) for both.

2. **Event 80 (Banco Sol) resolved as unscored** — see the Alignment section above. New `projects.evidence_complete` flag; Banco Sol is tracked but not scored.

3. **Cabinda refinery event re-typed** `completion` → `expansion` (it was an AIPEX award, not a completion); status kept and flagged — see above.

4. **`project_evidence` expanded** to the non-case-study projects via `db/_extract/expand_evidence.py` — **64 rows total, 48/50 scored projects covered** (see above).

5. **Read-only query API (`db/query.py`).** A dependency-free JSON access layer over the DB — filter by sector/province/organization/edition/status/score, with `--summary`, `--project <id>` (full timeline + orgs + field evidence), and `--facets` modes. Opens the DB immutable (never writes). This is the embryo of the "workflow integration" leg of the goal's framework (Unique Data + Decision Logic + Workflow Integration = Value) — the data is only useful if something can query it.

**Figure changes (both articles + `verify.py` updated together):** because Banco Sol (score 8) left the scored set and Cabinda (60→55) moved within it, the published figures shifted. The headline average moved **61 → 62** (precise 62.36 over 50 scored; was 61.39 over 51), the distribution **11/1/7/20/12 → 10/1/7/20/12**, the Energy sector **73,9 → 73,1**, the Finance sector **7 @ 38,9 → 6 @ 44,0** (Banco Sol was a Finance "announced" entry), and the private average **61,8 → 62,9** (government 54,3 unchanged). All 7 published case-study scores are unchanged. `verify.py` now also asserts the scored/unscored split (50/1) and that the unscored project is Banco Sol.

### Source re-linking applied 2026-07-25

The 80 events previously parked on the generic CIPRA source 15 were re-linked to specific articles, using source-to-event mappings extracted from the research files (see `db/_extract/`). Method: parallel subagents read each `research/*.md` file and returned the full-URL sources plus, per event, the URL grounded in the file text (strict no-fabrication instruction — only map when the file actually supports the link). `db/_extract/consolidate.py` dedups sources by URL, resolves event mappings (one URL per event, confidence-prioritized), keeps the existing hand-curated links (sources 1–14, 16), and retires the bogus generic-15 links.

Result on 104 events:

- **74 events** re-linked to a specific source (grounded URL from the research file).
- **8 events** manually recovered from source 15 — 4 (35, 36, 61, 79) to a full-URL source explicitly attributed in `status-trace-gov.md` but not auto-mapped by the agents; 4 (26–29) to a named publisher record with no pinned article URL (see Publisher-only sources above).
- **2 events** (14, 17) had their agent mapping overridden — the agent linked them to a clearly-wrong URL (the "linha verde" visa article for Banco Sol; a generic opening article for Brazil's return); the original specific source was kept instead.
- **13 events** set to NULL — no grounded source exists in the research files (the 2025 batch, the Huatong $900M expansion, two Chicomba follow-ups). Honest gaps, not fabrications.
- **1 event** (16) kept on source 15 — it is genuinely about that CIPRA article.
- Source 11 (a duplicate of source 15's URL) was dropped; its single event (16) remapped to 15.

The re-linking does **not** change execution scores — `source_id` is not an input to the scoring formula — so the score distribution (avg 61.4, 11/1/7/20/12) is unchanged.

### NULL-event grounding applied 2026-07-25

A targeted web-research pass (`db/_extract/link_null_events.py`) grounded 12 of the 13 previously-NULL events to real articles, adding 8 sources (ids 122–129). Each URL was either WebFetch-verified to resolve to the matching article, or (Angop) confirmed as a live indexed search result on the official press agency. No URL was fabricated.

| Event(s) | New source | Outlet | Confidence |
|----------|-----------|--------|------------|
| 75, 76 (Sonangol 2025 participation + Leão de Ouro) | 122 | Sonangol (official) | high |
| 77, 78 (ETU 2025 participation + Leão de Ouro, 25 yrs) | 123 | ETU Energias (official) | high |
| 81 (BDA new financing solutions FILDA 2025) | 126 | Correio da Kianda | medium |
| 82, 83 (BFA–Mashreq partnership announcement + operational) | 124 | BFA (official) | high |
| 84, 85 (AEP Portuguese delegation FILDA 2025 + completion) | 127 | Portugal Global / AICEP | high |
| 88 (Huatong $900M Barra do Dande port-terminal protocol) | 125 | 360 Angola | medium |
| 103 (Chicomba dam financing, presidential decree, BAI Europa + BCP) | 128 | Medafrica Times | medium |
| 104 (Chicomba dam construction launched) | 129 | Angop | high |

**Event 80 (Banco Sol Cartão Multicaixa Empresas)** stays NULL: the research and a web search surfaced only Banco Sol's year-round product pages, not a FILDA-2025 launch article — so the source is left unpinned rather than fabricated.

**Chicomba groundbreaking date corrected:** the source (Angop) reports the works launched on **Saturday 13 June 2026**, but event 104 had been dated `2026-07-19`. Corrected to `2026-06-13` in `events.csv`, and the published article updated to "13 de junho de 2026". The ~5-week shift did not change the score (57) — the delay term is year-based.

The grounding does **not** change execution scores (`source_id` is not a formula input); the distribution (avg 61.4, 11/1/7/20/12) is unchanged.

### Article reconciliation applied 2026-07-25

The published articles (private, not in this repo) were reconciled against the DB after re-linking. Scores were stable throughout this work (re-linking + the Chicomba date change moved no score), so every discrepancy found was pre-existing staleness in the published pieces, not a regression. The published figures and case-study scores were corrected to match the DB.

### Incremental update layer applied 2026-07-27

Resolves the goal doc's #1 strategic challenge: "make the data model event-driven so updates are incremental instead of periodic manual reviews." Maintenance is no longer a full CSV rebuild — the DB is the live working copy, CSVs are checkpoints, and every mutation is logged.

**New components:**
- `change_log` table (append-only audit trail) + `db_meta` table (checkpoint watermark `last_exported_at`) — both in `schema.sql` and loaded from `data/change_log.csv` / `data/db_meta.csv` by `load.py`, so the audit trail survives rebuilds and is git-diffable.
- `db/update.py` — append-only mutator. Subcommands `add-source`, `add-event`, `add-evidence`, `set-status`, `reverify`. Each mutating call requires a verifiable `--source-url` (no fabrication), recomputes only the affected project's score via `calculate_score`, and writes one `change_log` row inside the same transaction. Dry-run by default; `--apply` to persist. No-op mutations (duplicate source URL, duplicate `(project, field, source)` evidence) exit 0 **without** logging.
- `db/export_csv.py` — DB→CSV checkpointer. Exports all 8 tables, preserving each CSV's on-disk column order. The `projects.csv` `execution_score` column is exported from `compute_scores` (the formula value, not the stored column), so the checkpoint self-heals any recompute bug and `load.py`'s score gate stays a real backstop. Stamps `db_meta.last_exported_at` and writes an `export-csv` marker row.

**Staleness guard in `load.py`:** before the fresh rebuild, if the live DB has `change_log` **mutation** rows (`add-*`/`set-status`/`reverify`) newer than `db_meta.last_exported_at`, `load.py` refuses (the rebuild would silently lose them) unless `--force` is passed. The guard counts only mutations, not the `load-seed`/`export-csv` checkpoint markers, so a normal seed→append→checkpoint→seed cycle never trips it. Discipline: always `export_csv.py --apply` after `update.py --apply`.

**Banco Sol / event 80 exemption:** event 80 (`banco-sol-mc-empresas-2025`, `source_id IS NULL`, `evidence_complete=0`) was seeded, never appended. `update.py` requires `--source-url` for every `add-event`, so it can never attach a source to event 80 — the unscored-by-design state is preserved, not "fixed" by fabrication.

**Verifier impact:** `db/verify.py` gained 8 change_log/db_meta integrity checks (table presence, `last_exported_at` row, no orphan targets per operation, allowed-operation set) plus a watermark-lag warning — 77 checks total. The existing article-figure checks are untouched. Note: a real incremental edit to a scored project moves its score and therefore the published aggregates — `verify.py` will flag the count/avg/distribution drift by design (editing a case-study project = a published-figure update, not just a data edit); update the article + expectations together.

End-to-end verified 2026-07-27: dry-run all subcommands; `add-event --apply` inserted event 105 + source 130 (liveness-stamped) + recomputed score + `change_log` row + advanced `updated_at` via trigger; staleness guard refused the rebuild then passed after `export_csv.py --apply`; `load.py` round-trip reproduced the edit from the checkpoint CSV; `verify.py` caught the changed counts; no-op duplicate path wrote nothing.

### Formula versioning applied 2026-07-28

Resolves recommendation #14. Stored `execution_score` values are now stamped with the formula version that produced them, so a future weight change cannot silently make historical scores unreproducible.

- `calculate_scores.py` exposes `SCORE_VERSION = "v1-2026-07"` (the current weights); the breakdown dict returned by `calculate_score` now carries a `version` key (consumed by `update.py`'s `change_log` payload and `--verbose`).
- `load.py` stamps `db_meta.score_version` on every rebuild (`INSERT OR IGNORE`, warns on mismatch with the current constant). The score-consistency gate remains the real backstop: a weight change without a `--update-csv` re-snapshot fails the rebuild.
- `verify_invariants.py` asserts the `score_version` row exists and matches the constant (29 structural checks).
- `db_meta.csv` now carries two rows (`last_exported_at`, `score_version`); round-trips through `load.py` ↔ `export_csv.py`.

Weights are unchanged, so no score moved and no article figure drifted. The procedure for changing a weight (bump version → `--update-csv` → update `db/snapshot.json` via `verify_snapshot.py --update` + article together → reload + verify) is documented in `docs/scoring-methodology.md` § Versioning. (The v2 coverage-adjusted event-points variant described below has since been **adopted** as `v2-2026-07` — see the 2026-08-03 entry.)

### Huatong source mis-link corrected 2026-07-30

A residual source mis-link that survived the 2026-07-25 re-linking pass was caught and fixed. The Huatong Angola Industry project (`huatong-angola-industry-awards`, score 83) had its three 2026 events **and all four `project_evidence` rows** pointed at **source 16** — a Menos Fios article titled *"FILDA 2023 arranca hoje com economia digital em destaque"* (a 2023 FILDA-opening piece) that cannot ground any 2026 Huatong milestone. The score was unaffected (`source_id` is not a formula input — the invariance documented under Formula versioning), but the click-through evidence was wrong: a reader following the DB's source link for Huatong's $900M protocol or AIPEX awards landed on an unrelated 2023 article.

Fixed via the new `update.py relink` subcommand (see below), one transaction per row, all logged to `change_log`:

| Row | Was → now | Grounding |
|-----|-----------|-----------|
| event 31 (financing 2026-07-20) | 16 → 125 | 360 Angola "$900M Barra do Dande port terminal" (same date) |
| event 32 (completion 2026-07-17) | 16 → 54 | VerAngola "Huatong wins AIPEX Awards 2026" (same date) |
| evidence 1 (status=operational) | 16 → 54 | AIPEX Awards article (observed 2026-07-17) |
| evidence 2 (announced_value=$900M) | 16 → 125 | 360 Angola $900M article |
| evidence 3 (estimated_jobs=2000) | 16 → 125 | 360 Angola (1200 direct + 800 indirect) |
| evidence 4 (actual_completion=2029) | 16 → 125 | 360 Angola (completion 2029/2030) |
| event 16 (completion 2023-07-22) | 15 (unchanged) | CIPRA — genuinely about this article (FILDA 2023 prizes) |
| event 30 (construction 2026-01-01) | 16 → NULL → **130** | VerAngola, "Presidente inaugura fábrica de alumínio..." (15 Jan 2026 inauguration) — gap closed same day, see below |

The score held at 83 across all mutations (`score_old=score_new=83` logged in each `change_log` row) — the in-run proof of the `source_id` invariance.

**Event 30 gap closed 2026-07-30 (same day).** The construction event dated 2026-01-01 (Phase 1 inauguration / production start) was first **cleared to NULL** (the wrong link to source 16 could not be re-pointed without a verified replacement). A wider web search then surfaced the correct source: a VerAngola article from January 2026, *"Presidente inaugura fábrica de alumínio de investimento chinês de 250 milhões de dólares"* (the 15 Jan 2026 Lourenço inauguration at Barra do Dande — $250M Phase 1, 120k tons/year, 1,200 direct jobs, 5 phases, $1.6B total). This is the same outlet the published article attributes Huatong's production to. The URL was added as **source 130** (`add-source` stamped it `alive` — VerAngola blocks the WebFetch bot but not the stdlib fetcher used by `verify_sources.py`) and event 30 was re-linked to it. The facts were cross-verified against a second, independently fetchable outlet — an OPaís article dated 16 Jan 2026 reporting the 15 Jan inauguration with the same figures — so the grounding rests on more than one source. The NULL-event count returns to **1 (event 80, Banco Sol only)**; the "only Banco Sol is unsourced" invariant is restored.

**New `update.py relink` subcommand.** Re-links an existing `events` or `project_evidence` row to a different source (`--source-url`, idempotent on URL) or clears the link to NULL (`--clear`). It exists because re-linking is an UPDATE, not an append — the `add-*` subcommands only insert. It enforces the same no-fabrication rule (a real `--source-url` for any non-clear relink), recomputes the project's score inside the transaction, and logs one `change_log` row (`relink-event` / `relink-evidence` ops). A no-op (source unchanged) writes nothing. `verify.py` adds the two ops to its allowed set and two orphan checks (relinked rows must still point at a real row) — **81 checks total (was 79)**.

### Huatong April export event added 2026-07-30 (score 83 → 85)

The published Substack narrative had long cited Huatong's first 1,000-ton aluminum export to the Netherlands (April 2026, second 7,000-ton lot, ~240 tons/day, 500 tons sold domestically) as text, but the export milestone was not grounded in the DB — no event and no source pinned it. A fetch-verified April 2026 article from OPaís (*"Bengo inicia hoje a primeira exportação de alumínio produzido no parque industrial Huatong Angola"*, 13 Apr 2026 — 1,000 tons to the Netherlands in 40 containers, 7,000-ton second lot, ~240 tons/day, production increasing) was added as **source 131** and a new **event 105** (`expansion`, dated 2026-04-10) was appended to the Huatong timeline via `add-event --apply` (one `change_log` row, score recomputed in-transaction).

Unlike the 2026-07-30 mis-link fix (which held the score at 83 because `source_id` is not a formula input), this edit **does** move the score, because a new event type enters the formula: the `expansion` event flips the "awards/recognition (expansion events)" branch of the evidence bonus from 0 to +2 (per the Cabinda convention, `expansion` codes awards / recognition / new operational milestones). Huatong's event-points component was already saturated at the 30 cap (completion 15 + construction 8 + completion 15 + financing 7 = 45 → 30), so the +10 expansion points add nothing there; the +2 evidence bonus is the sole mover. **Score 83 → 85.** Breakdown: base 60 (operational) + events 30 (capped) + evidence 10 (jobs 3 + actual_completion 3 + prod_events 2 + expansion/award 2) − delay 15 (2023-07-22 → 2029) = 85.

A score change on a published case study is a cascade: the article↔DB contract required coordinated updates, done together — the published articles and `verify.py` expectations were updated in lockstep (case-study 83 → 85; avg 62.36 → 62.4; Manufacturing 84.5 → 85.0; source count 129 → 130; event count 104 → 105; linked 103 → 104). The overall average (62.4), private average (62.9), government average (54.3), and the distribution buckets (10/1/7/20/12) are unchanged — Huatong stays in the 81–100 band and the +2 is too small to move the one-decimal private average. The April-export claim was also re-grounded to its OPaís source (id 131) rather than the January production-start article it had cited.

### Award-completion re-type applied 2026-07-31

Integrity audit found 18 `completion` events that were actually awards (AIPEX 2026, Leão de Ouro 2022/2024/2025, BCI Challenge, Sonangol/SONILS). `completion` is the highest-weighted event (+15 vs `expansion` +10) and feeds `v_execution_by_sector.completion_rate`, so this inflated ~16 projects' scores 5–7 points and let award articles stand as completion evidence — violating "don't score unless someone can click through to completion evidence." All 18 re-typed `completion` -> `expansion` via the new audited `db/update.py retype-event` command (18 `change_log` rows; `--source-url` defaults to each event's own backing source, so the audit row always traces to a real URL).

One project (`angola-startup-summit-2023`, II Edition) was `status='completed'` backed only by the BCI Challenge award (event 12). With the award re-typed, the completed flag had no genuine completion evidence, so per the "only completed projects get the completed flag" rule it was downgraded to `operational` via `set-status` (score 98 -> 88; the summit series is ongoing — III edition held May 2024 — so `operational` is the correct status, sourced to the 2024 article).

Average 62.4 -> 60.7 (precise 60.68); distribution unchanged (10/1/7/20/12); private 62.9 -> 61.1 (government 54.3 unchanged). Sector averages that moved: Agriculture 36.8 -> 34.8, Energy 73.1 -> 70.0, Infrastructure 72.3 -> 69.0, Logistics 77.0 -> 72.0, Manufacturing 85.0 -> 82.0, Technology 71.3 -> 67.0, Telecom 72.8 -> 70.3. Only case study that moved: ETU 80 -> 75 (Huatong stayed 85 — at the 30-point event cap, so its two re-typed awards did not move it).

`verify.py` gained three checks: (A) hard-fail no `completion` event is an award (root-cause guard, via `constants.looks_like_award`); (B) hard-fail `completed`/`under_construction` backed by a genuine progress event (completion-non-award / construction / groundbreaking / financing); (C) warning `operational` without a genuine progress event — 22 operational projects are now tracked as known-open (15 exposed by this re-type — the award-only operational projects — plus 7 pre-existing operational-with-announcement-only), pending operational evidence or a status downgrade. The check count rose 81 -> 82. `retype-event` was added to `MUTATION_OPS` and the `change_log` orphan check. Articles, charts, README, and this lineage entry updated together per the score-cascade discipline.

Known follow-up (out of scope): the 22 Check C operational projects need either genuine operational evidence or a status downgrade — a separate research effort. The ~6 `completion` events that are conference/delegation/event completions (AEP delegations, Portugal-30-companies, etc.) are genuine completions of non-physical projects and are intentionally left as `completion`.

### 2026-08-04 — public-label band + external-block flag + API lineage

Three structural additions close the guideline gaps the project previously handled only with prose caveats:

- **`execution_band` (derived, not stored).** A coarse public-facing label — `UNCONFIRMED` / `STALLED` / `DELIVERED` / `IN_PROGRESS` / `SILENT` — computed in `db/query.py` from `status` + `execution_score` + `evidence_complete` via `db/constants.py:execution_band()`. Hybrid derivation: status sets the band, the score refines the upper bands (see `docs/scoring-methodology.md` § Execution Band). The 0–100 score stays as the analytical detail; the band is the primary published category. No score moved (it is a pure function of existing fields), so no snapshot/version bump. Exposed in `project_row()` and as `by_band` in `summary()`.
- **`is_externally_blocked` (label-only column).** A new `projects` column (INTEGER, default 0) flagging that a thin public trail reflects an external blocker (judicial / regulatory / disbursement), not inaction. **Not** a `calculate_score` input — the score is unchanged. Set via `db/update.py set-blocked --project ID --source-url URL --to 1` (idempotent, one `change_log` row, no score recompute). `set-blocked` added to `MUTATION_OPS`/`COMMANDS`; `verify_invariants.py` gained an orphan check for it (30 checks, was 29). All 51 projects default to 0 — none classified yet.
- **API lineage (Gap 1 cheap fix).** `summary()` and `project_detail()` now carry `score_version`; `project_detail()` adds a per-project `changelog` array (the `change_log` rows whose payload `project_id` matches). No `valid_from`/`valid_to` SCD2 time-ranges (the expensive part) — deliberately out of scope.

### 2026-08-04 — Tier 1 dataset cleanup (6 known-open warnings resolved)

The six `verify_invariants` Check-C warnings (`operational`/`under_construction` without a genuine progress event) and the BAI-parent recommendation (#8) were closed. Every mutation went through the audited `update.py` (relink / retype-event / set-status) with a real `--source-url`, or a documented `events.csv`/`organizations.csv` checkpoint edit; no figure was moved without source verification first.

- **anpg-oil-sector-grand-prize** (65 → 72): events 28/29 were backed by source 121, a URL-less low-confidence placeholder ("publisher cited, article URL not pinned"). Re-linked to real articles — event 28 (Block 33/24 development agreement, signed at **AOG 2025**, not FILDA) to source 132 (angolaoilandgas.com); event 29 ($100B pipeline / Q1-2026 $7.16B revenue) to source 133 (angolanminingoilandgas.com). Event 28 re-typed `expansion` → `financing` (signed Risk-Services Contract = capital committed) — a genuine progress event, so the `operational` status is now backed (no downgrade). No score version bump (re-typing events changes the score but not the formula weights).
- **angola-startup-summit-2023** (78 → 94): event 37 ("III edition held") was linked to a *preview* article ("Arranca hoje") that does not confirm the event happened. Re-linked to the post-event winners article (source 134, menosfios) and re-typed `expansion` → `completion` (non-physical completion, per the AEP-delegation convention). Source verification found a real post-event source (winners named, INAPEM-IMA memo); the original preview would have made a `completion` re-type a fabrication.
- **brazil-returns-to-filda** (75 → 30): participation-only (4 companies via ApexBrasil), no progress event → downgraded `operational` → `announced` (sourced to RFI, the original announcement).
- **pt-ao-credit-line-2-5b** (69 → 65): event 61 (the 2.5B→3.25B increase) re-typed `expansion` → `financing` (capital committed). **Source correction:** events 61 and 79 were mis-linked to source 91 (a 23-Nov-2025 *Jornal de Negócios* retrospective about the AU-EU summit) which does not mention FILDA; the increase was actually announced **25 July 2025** during FILDA 2025 week (40th edition, 22–27 Jul), confirmed by portugal.gov.pt, VerAngola, Observador, Público, and Forbes. Both events re-linked to source 135 (portugal.gov.pt, 25 Jul 2025); the original 2025-07-25 date and the "during FILDA week" framing are correct and retained. The first-pass plan to drop the FILDA framing was based on the mis-linked source and was reverted. The within-2025 date shift is year-based, so the re-type (not the date) is the sole score mover.
- **huatong-aipex-awards-2026** (76 → 83): event 88 ($900M Barra do Dande port-terminal protocol, 25-year concession) re-typed `expansion` → `financing` (signed protocol; disbursement conditional on regulatory approval). Source 125 (360angola) confirms the signed protocols. This project is distinct from the case study `huatong-angola-industry-awards` (score 78, unchanged).
- **cabinda-refinery-aipex-2026** (54 → 29): the project is scoped as the **AIPEX Energy Award at FILDA 2026** (an award project), not the refinery build — downgraded `under_construction` → `announced` for consistency with other award-only projects. (The refinery itself was inaugurated Sep 2025 and began commercial production Apr 2026 — a genuinely delivered project, but a different scope, flagged as a Tier 3 coverage candidate.)
- **ev79 / pt-ao-credit-line-3-25b**: same mis-link and same unsupported "FILDA week" framing as ev61, on the same Nov-2025 source — re-linked to source 135 alongside ev61 for consistency. Score 8 unchanged (year-based delay).
- **BAI parent (recommendation #8):** added the `bai` org (Banco Angolano de Investimentos, Angola) to `organizations.csv` with the bancobaieuropa.pt source, and set `bai-europa.parent_org_id = 'bai'` (99.99% subsidiary). The `organizations` table has no `source_id` column, so the source is recorded here and in the description, not as a DB source link.

**Figure cascade (articles + snapshot + this lineage updated together):** avg 43.78 → 42.90 (44 → 43); distribution 16/12/2/13/7 → 16/14/1/10/9; private avg 43,1 → 42,2 (government 54,0 unchanged); sources 130 → 134. Sector averages that moved: Energy 35,0 → 32,4; Finance 28,8 → 28,2; Manufacturing 65,3 → 67,0; Technology 53,3 → 57,3; Multi-sector 57,6 → 52,0. Only published case-study score that moved: `pt-ao-credit-line-2-5b` 69 → 65 (Huatong 78, Linha Verde 3, Chicomba 50, portal 81, ETU 30, credit-line-3.25B 8 all unchanged). `verify_invariants` now 30/30 with **zero known-open warnings** (was 6). `db/snapshot.json` regenerated; all five article variants (EN-full, PT-full, Substack, LinkedIn-EN, LinkedIn-PT) + `make_charts.py` updated; article pin green.

### 2026-08-04 — Tier 3 coverage expansion: source_program schema + AIPEX cohort (6 projects)

The database broadens beyond FILDA for the first time. A new `projects.source_program` column (TEXT NOT NULL DEFAULT 'FILDA') tags the announcement channel: `FILDA` (the original 51, backfilled), `AIPEX`, `refinery`, `PPP`, `multilateral` (single-sourced in `constants.SOURCE_PROGRAMS`). `filda_edition` is NULL for non-FILDA projects. Schema change touched `schema.sql` (column + `idx_projects_program` index), `load.py` + `export_app_json.py` column lists, `query.py` (SELECT, project_row, `--source-program` filter, facets), `verify_invariants.py` (allowed-set check, +1 → 32 checks), `data-model.md`. Verified NULL `filda_edition` breaks nothing (non-FILDA projects are simply excluded from `--edition` filters; the facet shows a NULL bucket). No score moved (source_program is not a formula input).

The first Tier 3 cohort: **5 AIPEX-promoted investment projects** from the AIPEX May-2024 six-contract signing (investinangola.ao, source 136) — Huatong excluded (already tracked) — plus the **cabinda-refinery-build** project (the refinery itself, distinct from the AIPEX-award project `cabinda-refinery-aipex-2026` already in the DB). All 6 added with `source_program` = AIPEX / refinery; `filda_edition` = NULL. Projects/orgs/links added via CSV edits (no `update.py add-project` command exists); events added via the audited `update.py add-event` (each `--source-url`, `change_log` row).

Per-project outcomes (2 years on from the May-2024 signing):
- **quilemba-solar-park** (Energy/AIPEX) — 47/100, `under_construction`: construction started May 2025 (BusinessWire, source 137), 35 MWp, 95% complete Jun 2026, commercial operation expected Jul 2026. JV TotalEnergies 51% / Sonangol 30% / Maurel & Prom 19%.
- **baia-fish-processing** (Agriculture/AIPEX) — 69/100, `operational`: inaugurated 8 Jun 2026 (VerAngola, source 138), 200 t/day, BDA+BPC $18M.
- **cabinda-refinery-build** (Energy/refinery) — 76/100, `operational`: Phase 1 (30,000 bpd) financed 2022 (Gemcorp equity + AFC $150M + Afreximbank $100M; Africa Oil & Gas Report, source 139), built 2022 (Angola Petroleum, source 140), inaugurated Sep 2025, commercial production Apr 2026.
- **safcomex-cooking-oil** (Agriculture/AIPEX) — 8/100, `announced`: $25M soya/cooking-oil/bran factory, Icolo e Bengo; expected operational end-2024 but no confirmation found (honest low score).
- **hospital-serum-factory** (Manufacturing/AIPEX) — 8/100, `announced`: $15M, 503 jobs. The separate larger VitalFlow EUR 80M pharmaceutical complex in the ZEE is a **different project** (different value/scope/jobs) — not conflated; flagged here to avoid a future mix-up.
- **ponto-mais-furniture** (Manufacturing/AIPEX) — 8/100, `announced`: $10.3M, 77 jobs, furniture (Casa Nova group); no execution evidence found.

**Figure cascade (57 tracked / 56 scored):** avg 42,90 → 42,16 (43 → 42); distribution 16/14/1/10/9 → 19/14/2/12/9; private avg 42,2 → 41,5 (government 54,0 unchanged, 3 vs 53); sources 134 → 139; events 105 → 115. Sector averages that moved: Agriculture 16,4 → 22,7 (+safcomex 8, +baia-fish 69), Energy 32,4 → 38,9 (+quilemba 47, +cabinda-refinery 76), Manufacturing 67,0 → 47,3 (+hospital-serum 8, +ponto-mais 8). No case-study score moved (the 7 case studies are existing projects, unchanged). `db/snapshot.json` regenerated; `app/data.json` synced; articles + this lineage updated; verify_invariants 32/32, 0 known-open warnings. `hospital-serum-factory` has no org link (no confirmed investor in the AIPEX announcement) — 56 of 57 projects have org links.

---

## Scoring Layer: Formula Application

### How scores are computed

```
Score = Base(status) + Events(max 30) + Evidence(max 10) - Delay(-5 to -15) - StatusPenalty(-10 to -15) - OnlyAnnouncement(-10)
Clamped to [0, 100]
```

### Score distribution

Average score: **42.16 over 56 scored projects** (Banco Sol is tracked but `evidence_complete = 0` / unscored; matches the figure cited in the published article). Under v2-2026-07 (distinct-type event points + confidence-weighted evidence bonus + 17 operational-without-progress downgrades + 2026-08-04 Tier 1 cleanup + 2026-08-04 Tier 3 AIPEX cohort).

| Range | Count | % | Interpretation |
|-------|-------|---|----------------|
| 0-20 | 16 | 32% | No evidence of execution beyond announcement |
| 21-40 | 12 | 24% | Initial or partial execution |
| 41-60 | 2 | 4% | Operational but no traceable timeline in sources consulted |
| 61-80 | 13 | 26% | Substantive execution with verifiable results |
| 81-100 | 7 | 14% | Strong execution with documented results |

### Known scoring issues

1. **~~Source concentration in event timelines~~** — resolved by re-linking (2026-07-25). Source 15 no longer dominates; no single source backs more than ~14% of events, and each is genuinely covered by that source. The remaining structural bias — that better-covered projects record more events and therefore score higher — remains inherent to the formula, but it no longer reflects a single outlet.

2. **Base score can exceed 100 before penalties** — e.g., completed (70) + 30 events + 10 evidence = 110, then clamped to 100. The formula works but the weights aren't independent.

3. **Delay penalty uses event dates, not announcement dates** — projects without dated events have no delay penalty applied, which may understate delays.

4. **Evidence bonus is partially automated** — the script checks for estimated_jobs, actual_completion, and event types, but doesn't verify specific outcomes like "export documented" or "revenue reported." These would need manual tagging.

5. **Recency / survivorship bias in the edition averages** — the mean rises by edition (2022: 51.4 → 2026: 80.0), but part of that rise is mechanical: older projects have lost press trail and therefore record fewer dated events and earn fewer event points (issue #1). The trend is a coverage-contaminated signal, not a clean measure of improving execution. See `docs/scoring-methodology.md` § Limitations #5.

---

## Article Layer: Claim Traceability

### Claims in the published pieces and their data lineage

The table below maps each published claim to the DB source IDs that back it after the 2026-07-25 re-linking, so the published pieces and the structured record are traceable to each other. (The published articles are private — not in this repo — so their internal footnote numbering is not shown here.)

| Published claim | Data Source | DB Table | DB source IDs (post-re-link) | Confidence |
|-----------------|-------------|----------|------------------------------|------------|
| 630 participations (2022) | Correio Digital + Menos Fios | research file | — (research-file claim, no single DB row) | medium |
| 2,348 participations (2026) | VerAngola | research file | — (research-file claim) | high |
| Average score 42 (56 scored) | calculated | projects.execution_score | n/a | formula-derived (DB avg 42,16; Banco Sol unscored) |
| 57 projects tracked (56 scored) | count | projects | n/a | direct count (evidence_complete) |
| Distribution table (19/14/2/12/9) | calculated | projects grouped by score | n/a | formula-derived, 56 scored |
| Sector table | calculated | projects grouped by sector | n/a | formula-derived (Agriculture 22,7; Energy 38,9; Manufacturing 47,3; Technology 57,3; Multi-sector 52,0) |
| Gov vs private (54,0 vs 41,5) | calculated | projects by sector | n/a | derived from 56 scored obs (government = `Government` sector, 3 projects; private = other 53) |
| Huatong score 78 | calculated | projects + events + sources | 15, 54, 125, 130, 131 (16 removed 2026-07-30; event 30 grounded via 130; event 105 Apr-2026 first export grounded via 131 — see Huatong April export) | high |
| Linha Verde score 3 | calculated | projects + events + sources | 1, 67 | high |
| Credit line 2.5B score 65 (and successor 3.25B score 8) | calculated | projects + events + sources | 36, 135 (3.25B: 135, relinked from 91) | high (timeline now grounded) |
| Chicomba score 50 | calculated | projects + events + sources | 38, 128, 129 | high |
| Participation methodology (direct + indirect) | research file | filda-participation-analysis.md | — (research file) | high (6 sources) |

**Note on the credit-line score:** the earlier version of this table listed "Credit line score 50 / projects (no events)". That is stale — the credit-line projects now have grounded events (the Portugal.gov.pt FILDA-2024 announcement, source 36; the Jornal de Negócios credit-increase article, source 91), so the 2.5B line scores 70 and the 3.25B successor scores 53.

The four illustrative case studies are Huatong, the Portugal–Angola credit line, Chicomba, and Linha Verde. (The Investment Portal is also DB-tracked at score 83, backed by DB sources 1 and 70.)

---

## Recommendations

### Done

1. ~~Enter events for all projects without events~~ — resolved. All 51 projects now have event timelines.
2. ~~Enter organization links for all projects without org links~~ — resolved. All 51 projects have org links.
3. ~~Make the CSV → SQLite pipeline reproducible~~ — resolved. `db/load.py` rebuilds the database from `schema.sql` + `data/*.csv`, enforces foreign keys on completion, and exposes integrity errors that were previously hidden.
4. ~~Add the 11+ sources used in research but not in the sources table~~ — resolved. 105 sources extracted from the research files and loaded (120 total); see Source Layer.
5. ~~Re-link the 80 CIPRA-generic events to their specific articles~~ — resolved. 91/104 events now link to a specific source; source 15 backs exactly 1 event; see "Source re-linking applied 2026-07-25".

### Governance hardening (2026-07-25)

16. ~~**Run `foreign_key_check` in the loader**~~ — resolved. `db/load.py` now runs `PRAGMA foreign_key_check` after re-enabling FKs and fails the load on any violation. See "Governance hardening applied 2026-07-25".

17. ~~**Single source of truth for `execution_score`**~~ — resolved. `db/load.py` recomputes scores and asserts they match the `projects.csv` snapshot (score-consistency gate); `db/calculate_scores.py --update-csv` refreshes the snapshot. The DB ships only formula-verified scores.

18. ~~**Reconcile the scoring methodology docs to the code**~~ — resolved. `docs/scoring-methodology.md` matches `calculate_scores.py` (evidence-bonus rule, formula terms, reproducible worked examples 78/3/81 under v2-2026-07); the stale formula in `docs/data-model.md` was replaced with a pointer. The confidence-weighting of evidence is now implemented as of v2-2026-07.

19. ~~**Pin article figures to the DB (regression net)**~~ — resolved. `db/verify_snapshot.py` derives every published figure from the DB and compares to committed `db/snapshot.json`, and pins article text to DB figures; exits non-zero on drift. This subsumes the manual reconciliation behind recommendation #11 and the ETU check behind #12.

### Alignment work (2026-07-25)

20. ~~**Stop overwriting `created_at`**~~ — resolved. `load.py` now persists `created_at` from CSV (rebuilt fresh each run); existing rows backfilled with the 2026-07-23 dataset-origin date. See "Alignment work applied 2026-07-25".

21. ~~**`last_verified` + source liveness**~~ — resolved. `projects.last_verified` (hand) and `sources.last_verified` + `url_status` (auto via `db/verify_sources.py`) added. First pass: 114 alive / 4 blocked / 7 dead — the dead/blocked links are flagged for `archived_url` backfill.

22. ~~**Field-level provenance (starter)**~~ — `project_evidence` table added and backfilled for the 7 case studies (14 rows). The remaining 44 projects are field-evidence-pending; the `source_id` convention is documented and not overclaimed.

23. ~~**"No score without click-through evidence"**~~ — operationalised as a `verify_invariants.py` check. Event 80 (Banco Sol) is the single known exception, surfaced every run as a warning (the event appears misattributed, not merely unsourced) pending a decision.

### To improve data quality

6. ~~**Pin sources for the 13 NULL events**~~ — 12 of 13 grounded via targeted web research (2026-07-25); only event 80 (Banco Sol) remains NULL. See "NULL-event grounding applied 2026-07-25".

7. ~~**Correct the Chicomba groundbreaking date**~~ — resolved (2026-07-25). Event 104 now dated `2026-06-13` per the Angop source; the published article was updated to "13 de junho de 2026". Score unchanged (57).

8. **Add the BAI parent organization** — `bai-europa`'s `parent_org_id` was nulled because the `bai` parent is not in the dataset. Add it with a source to restore the subsidiary relationship.

9. **Tag evidence fields explicitly** — add columns or tags for "jobs_verified", "production_started", "exports_documented", "awards_won" instead of inferring from event types.

10. **Add a "data_completeness" field per project** — flag projects where only announcement data exists vs those with full timelines.

### To improve traceability

11. ~~**Re-cite the article against the new source IDs**~~ — resolved (2026-07-25). The Article Layer table now maps each published claim to the post-re-linking DB source IDs, and stale numeric claims were refreshed (avg 46→61, private/gov 46.5/34.3→61.5/54.3, credit-line "score 50 / no events"→70/53 with grounded events).

12. ~~**Reconcile the published ETU score**~~ — resolved (2026-07-25). The published piece cited ETU Energias at 85/100 but the DB computes 80/100 (a pre-existing mismatch — the score was stable across re-linking). Corrected to 80. See "Article reconciliation applied 2026-07-25".

13. **Every number in the article should link to a source ID** — currently some derived statistics (distribution table, sector averages) are formula-derived but the underlying project data should still be traceable.

14. ~~**Version the scoring formula**~~ — resolved (2026-07-28). `calculate_scores.SCORE_VERSION` is stamped into `db_meta.score_version` by `load.py` and asserted by `verify_invariants.py`; the change procedure is documented in `docs/scoring-methodology.md` § Versioning. See "Formula versioning applied 2026-07-28".

15. **Add a "last_verified" date per project** — when was the last time a human checked the project status against current sources?