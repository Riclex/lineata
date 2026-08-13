# Execution Methodology — How Scores Are Produced

**Methodology version:** v1 (August 2026). References the scoring formula version `v2-2026-07` (see §7).

This document explains, in plain language, how the Angola Investment Execution Database decides what it knows and how it produces an execution score. It is written for readers of the analyses — investors, lenders, journalists, policymakers, researchers — so that any score can be checked and, if wrong, challenged with evidence.

The exact formula weights, worked examples, and version history live in [`scoring-methodology.md`](scoring-methodology.md) (engineering reference). The executable formula is `db/calculate_scores.py` in this repository.

---

## 1. What this methodology governs

The database tracks announced investment projects in Angola and records — as dated, sourced events — what actually happened to them. Each project receives an **execution score from 0 to 100**.

What the score measures: the strength of the **verifiable execution timeline** — whether the public record shows the project moving from announcement toward delivery. It does not measure corporate solvency, management quality, or the value of the investment.

Two claims the score never makes:

- **A low score is not a judgment that a project failed.** It means the public record consulted does not show a trail of execution. Absence of information is not proof of absence of execution.
- **A high score is not a guarantee of future success.** It means the public record shows a project that has demonstrably moved.

---

## 2. Evidence collection & verification hierarchy

Every project record is built from dated, sourced observations. The database does not record an event (a financing, a groundbreaking, a completion) unless it is grounded in a source that can be clicked through and checked.

### The no-fabrication rule

No source URL is ever invented. Every event and every evidence item is attached to a real, working URL, checked before it is recorded, and stored with a pointer to its source record. If a milestone cannot be grounded in a verifiable source, that milestone is not recorded — and where the announcement itself cannot be grounded, the project is tracked but **not scored** (it stays in the historical record, flagged `UNCONFIRMED`, and is excluded from published averages).

### Verification hierarchy

Sources carry a confidence label (high / medium / low). The hierarchy, strongest first:

1. **Official registries and filings** — government gazettes, regulatory filings, official registries
2. **Institutional project pages** — World Bank, African Development Bank, Global Fund, and similar progress reports
3. **Company disclosures** — annual reports and official company announcements
4. **Procurement and concession documents** — procurement notices, signed awards, concession terms
5. **Permits and licensing records** — construction permits and other administrative records
6. **Reputable news media** — press coverage of the specific event
7. **Unverified or aggregator content** — lowest weight

The confidence label is assigned when a source is entered and is part of the record. Where a score component depends on a source, higher-confidence sources count for more.

### Source hygiene

- Each source record keeps an **archived copy** link (web.archive.org / archive.today) where one is available, so a page that later disappears does not take the evidence with it.
- A **liveness check** periodically verifies each URL still resolves; the result and the last-checked date are stored on the source record. Dead links are flagged, not silently dropped.

---

## 3. Scoring framework

The execution score is computed automatically by a formula over the database. The same data always produces the same score, and the formula is versioned so historical scores remain reproducible.

Four inputs (plain language — exact weights in `scoring-methodology.md`):

1. **Current status.** Where the project sits today (announced, financed, under construction, operational, completed, delayed, suspended…). Delivered states score higher than announced states.
2. **Timeline events.** What dated milestones the record shows (MoU, financing, groundbreaking, construction, completion, expansion…). A rich, diverse timeline scores higher than a lone announcement; repetitive coverage of the same milestone is not double-counted.
3. **Results evidence.** Verifiable outcomes attached to the project — a job figure, a recorded completion date, evidence of production or expansion. These are weighted by the confidence of the source that backs them.
4. **Penalties.** The formula deducts for delay since announcement, for a publicly "delayed" or "suspended" status, and for projects whose **only** dated event is the announcement itself — the "announced and nothing else" case.

The score is clamped to the 0–100 range.

**An important property.** The formula does not depend on *which* individual source an event is linked to — only on the event's date and type, the project's fields, and (for the small results-evidence component) the confidence label of the backing source. Re-linking an event to an equivalent source never changes a score. This keeps the score independent of how much research effort went into a project.

---

## 4. Confidence & uncertainty

Scores are published alongside a coarse **execution band** — a plain-language category that is honest about what public records can support:

| Band | Meaning |
|------|---------|
| **DELIVERED** | Completed, or a strong verifiable execution record |
| **IN_PROGRESS** | Operational, under construction, or restarted with evidence |
| **STALLED** | Publicly delayed or suspended |
| **SILENT** | Announced or unknown with a thin public trail — read as "the public record is thin", not "the project failed" |
| **UNCONFIRMED** | Tracked but not scored; no verifiable source could be found |

A project may also carry an **externally blocked** flag when its thin trail reflects a judicial, regulatory, or disbursement blocker rather than inaction. That flag is a label; it does not change the score.

---

## 5. Review & dispute process

The database keeps an **append-only audit trail**. Every change — a new event, a new source, a re-verification, a status change — writes one dated, searchable audit row recording what changed and which source authorized it. Nothing is silently overwritten.

If a reader believes a score is wrong:

1. They point to a source the database missed (a gazette, a filing, a news report).
2. The project is re-examined against that source; the finding is recorded with the source attached.
3. If the evidence changes the record, the change is applied, logged, and the score recomputed by the formula.

Corrections are normal and expected. The methodology is designed so a correction is a dated, auditable event — not a silent edit.

---

## 6. Limitations, stated plainly

1. **Documentation advantage.** Institutions that publish project pages and progress reports leave an easier trail to find. Better-documented projects score higher, all else equal — a coverage effect, not pure execution quality.
2. **Absence is not proof.** A low score means no observable public footprint in the sources consulted, not demonstrated failure. Under-reported projects are revisited as new sources appear.
3. **Small samples.** Averages over tiny groups can be swung by a single project; they are published with their sample sizes so they can be read accordingly.
4. **Media-coverage bias.** Heavily covered projects accumulate more events. The formula caps the events contribution precisely to limit this.
5. **Time window.** Recent announcements have had less time to demonstrate execution. A young project that scores low may simply be young.

---

## 7. Versioning & change policy

Every stored score is stamped with the **formula version** that produced it. When the formula changes, the version is bumped, all scores are recomputed in one pass, and the change is documented in the version history before the new numbers are published. Scores from an older version remain reproducible because the data, the formula, and the version stamp are all retained.

The current formula version is `v2-2026-07`; its adoption history and the weight-change procedure are recorded in `scoring-methodology.md`.

---

## 8. Governance & lineage

- The entire pipeline is **public and reproducible**: the schema, the loading code, the scoring code, the verification suite, and the raw CSV checkpoints live in this repository. A fresh clone rebuilds the database from scratch.
- Source extraction is **assisted by AI models**; every score and every primary key is cross-verified against a deterministic, click-through source URL before publication.
- Published analyses state their **method, sample sizes, and limitations** in the same place as their findings — so the reader can weigh the evidence alongside the number.
