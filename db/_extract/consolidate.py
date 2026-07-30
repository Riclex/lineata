#!/usr/bin/env python3
"""Consolidate agent-extracted sources + event mappings into sources.csv/events.csv.

Rules:
- Keep existing sources 1-16 (real specific sources). For the duplicate URL shared
  by ids 11 and 15, canonicalize to 15 and drop row 11.
- Add new sources (ids 17+) deduped by URL against existing.
- Event mapping: for events currently on the generic source 15 (known-wrong for
  non-2023-jobs events), replace with the agent's grounded URL if present, else NULL.
- Events already on a specific source (1-14,16) are KEPT (avoid regression), except
  event(s) pointing to dropped id 11, which remap to 15.
- Print a full report; write nothing unless --apply.
"""
import csv, json, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data")
EXTRACT = os.path.join(BASE, "db", "_extract")

GENERIC_SOURCE_ID = 15          # the bogus "everything points here" CIPRA link
DUP_URL_CANONICAL = {           # url -> preferred existing id when multiple ids share a url
    "https://cipra.gov.ao/noticias/838/governo/filda-2023/a-maior-bolsa-de-negocios-gera-mais-de-mil-empregos-para-jovens": 15,
}
DROP_EXISTING_IDS = {11}        # duplicate of 15 (same URL)

# ---- load existing sources ----
existing = {}
order = []
with open(os.path.join(DATA, "sources.csv"), newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        existing[int(row["id"])] = row
        order.append(int(row["id"]))

url_to_id = {}
for sid, r in existing.items():
    if sid in DROP_EXISTING_IDS:
        continue
    u = r["url"]
    if u in DUP_URL_CANONICAL:
        url_to_id[u] = DUP_URL_CANONICAL[u]
    elif u not in url_to_id:
        url_to_id[u] = sid

# ---- load agent extracts ----
agent_files = ["2022.json", "2023.json", "2024.json", "2026.json", "gov.json", "trade.json"]
all_sources = []   # list of source dicts (url,title,date,publisher,confidence)
all_mappings = {}  # event_id -> list of (url, confidence, file)
for fn in agent_files:
    with open(os.path.join(EXTRACT, fn), encoding="utf-8") as f:
        d = json.load(f)
    file_tag = fn.replace(".json", "")
    for s in d.get("sources", []):
        all_sources.append(s)
    for m in d.get("mappings", []):
        eid = m["event_id"]
        url = m["url"]
        # find confidence for this url among collected sources
        conf = "medium"
        for s in d["sources"]:
            if s["url"] == url:
                conf = s.get("confidence", "medium"); break
        all_mappings.setdefault(eid, []).append((url, conf, file_tag))

# ---- assign ids to new sources (dedup by url) ----
next_id = max(existing) + 1
new_rows = []
for s in all_sources:
    u = s["url"]
    if not u or "..." in u or u.endswith("/..."):
        continue  # drop junk/placeholder URLs
    if u in url_to_id:
        continue
    nid = next_id
    next_id += 1
    url_to_id[u] = nid
    new_rows.append({
        "id": nid, "title": s.get("title", ""), "url": u,
        "date": s.get("date", ""), "publisher": s.get("publisher", ""),
        "archived_url": "", "confidence": s.get("confidence", "medium"),
    })

# ---- manual recoveries (grounded in research text but not auto-mapped by agents) ----
# Full-URL sources present in agent extracts and explicitly attributed in the research
# file, but no agent created an event mapping for these specific events.
MANUAL_URL_MAP = {
    35: "https://cip.org.pt/cip-reforca-cooperacao-economica-entre-portugal-e-angola/",
    36: "https://forbesafricalusofona.com/agencia-de-investimento-privado-e-promocao-das-exportacoes-de-angola-assina-tres-acordos-de-cooperacao-com-entidades-portuguesas/",
    61: "https://www.jornaldenegocios.pt/economia/mundo/africa/angola/detalhe/portugal-assinou-23-instrumentos-de-cooperacao-com-angola-e-reforcou-credito-em-62-desde-julho-2024",
    79: "https://www.jornaldenegocios.pt/economia/mundo/africa/angola/detalhe/portugal-assinou-23-instrumentos-de-cooperacao-com-angola-e-reforcou-credito-em-62-desde-julho-2024",
}
# Agent mapped these to a clearly-wrong URL; keep the original specific source.
MANUAL_KEEP = {14: 9, 17: 12}
# Events grounded to a named publisher in status-trace-companies.md but with no full
# article URL in the research. Create a no-URL source record per publisher to preserve
# the honest attribution (publisher known, exact article not yet pinned).
MANUAL_PUBLISHER = {
    26: ("Jornal de Negócios", "low"),            # Sonangol profits fell 11% in 2025
    27: ("Reuters", "low"),                        # Sonangol $4.8B China loan (Feb 2026)
    28: ("Angolan Mining Oil & Gas", "low"),       # ANPG Block 33/24 dev agreement
    29: ("Angolan Mining Oil & Gas", "low"),       # ANPG $100B pipeline / Q1 2026 revenue
}

publisher_id = {}  # event_id -> source id
pub_to_id = {}     # publisher name -> source id (dedup)
for eid, (pub, conf) in MANUAL_PUBLISHER.items():
    if pub not in pub_to_id:
        nid = next_id
        next_id += 1
        new_rows.append({
            "id": nid, "title": f"{pub} — publisher cited, article URL not pinned",
            "url": "", "date": "", "publisher": pub,
            "archived_url": "", "confidence": conf,
        })
        pub_to_id[pub] = nid
    publisher_id[eid] = pub_to_id[pub]

# ---- load events ----
events = []
with open(os.path.join(DATA, "events.csv"), newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fields = reader.fieldnames
    for row in reader:
        events.append(row)

CONF_RANK = {"high": 3, "medium": 2, "low": 1}
# prefer status-trace files for follow-up event types
FOLLOWUP = {"delay","suspension","restart","completion","expansion",
            "groundbreaking","construction","financing","ownership_change","closure"}

def pick_url(eid):
    cands = all_mappings.get(eid)
    if not cands:
        return None
    etype = next((e["event_type"] for e in events if int(e["id"])==eid), "")
    def score(c):
        url, conf, tag = c
        s = CONF_RANK.get(conf, 2)
        if etype in FOLLOWUP and tag in ("gov","trade"):
            s += 0.5
        if etype in ("announcement","mou") and tag in ("2022","2023","2024","2025","2026"):
            s += 0.5
        return s
    cands.sort(key=score, reverse=True)
    return cands[0][0]

# ---- decide new source_id per event ----
decisions = []
null_events = []
for e in events:
    eid = int(e["id"])
    cur = int(e["source_id"]) if e["source_id"] else None
    new_src = None
    note = ""
    if eid in MANUAL_KEEP:
        new_src = MANUAL_KEEP[eid]
        note = f"manual keep {cur} (agent mapping was wrong)"
    elif eid in MANUAL_URL_MAP:
        u = MANUAL_URL_MAP[eid]
        new_src = url_to_id.get(u)
        note = f"manual recover->{u[:55]}"
    elif eid in publisher_id:
        new_src = publisher_id[eid]
        note = f"publisher source: {MANUAL_PUBLISHER[eid][0]}"
    else:
        mapped_url = pick_url(eid)
        if mapped_url:
            new_src = url_to_id.get(mapped_url)
            note = f"mapped->{mapped_url[:60]}"
        elif cur == GENERIC_SOURCE_ID:
            new_src = None  # retire bogus generic link
            note = "NULL (was generic 15, no grounded source)"
            null_events.append(eid)
        elif cur in DROP_EXISTING_IDS:
            new_src = DUP_URL_CANONICAL.get(existing[cur]["url"])
            note = "remap dropped 11->15"
        else:
            new_src = cur  # keep existing specific source
            note = f"keep {cur}"
    decisions.append((eid, e["project_id"], e["event_type"], cur, new_src, note))

recovered = len(MANUAL_URL_MAP) + len(MANUAL_PUBLISHER)

# ---- report ----
print(f"Existing sources kept: {len(existing)-len(DROP_EXISTING_IDS)} (dropped ids {DROP_EXISTING_IDS})")
print(f"New sources added: {len(new_rows)} (ids {min(r['id'] for r in new_rows) if new_rows else '-'}..{max(r['id'] for r in new_rows) if new_rows else '-'})")
print(f"Total sources after: {len(existing)-len(DROP_EXISTING_IDS)+len(new_rows)}")
print(f"Events remapped to new source: {sum(1 for d in decisions if d[4] and d[4]!=d[3] and 'keep' not in d[5])}")
print(f"Events manually recovered from generic 15: {recovered} (4 to full URL, 4 to publisher-no-URL)")
print(f"Events -> NULL (ungrounded, was generic 15): {len(null_events)}  ids={null_events}")
print()
print("=== per-event decision (changed only) ===")
for eid,pid,etype,cur,new,note in decisions:
    if cur != new or "NULL" in note:
        print(f"  {eid:>3} {pid:<34} {etype:<13} {cur}->{new}  {note}")

if "--apply" not in sys.argv:
    print("\n(dry run — nothing written. re-run with --apply to write sources.csv + events.csv)")
    sys.exit(0)

# ---- write sources.csv ----
with open(os.path.join(DATA, "sources.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["id","title","url","date","publisher","archived_url","confidence"])
    w.writeheader()
    for sid in sorted(existing):
        if sid in DROP_EXISTING_IDS: continue
        r = existing[sid]
        w.writerow({k: r.get(k,"") for k in ["id","title","url","date","publisher","archived_url","confidence"]})
    for r in new_rows:
        w.writerow(r)

# ---- write events.csv ----
dec_map = {d[0]: d[4] for d in decisions}
with open(os.path.join(DATA, "events.csv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    for e in events:
        eid = int(e["id"])
        e["source_id"] = str(dec_map[eid]) if dec_map[eid] is not None else ""
        w.writerow(e)
print("\n[OK] wrote sources.csv and events.csv")