#!/usr/bin/env python3
"""Ground the 13 previously-NULL events to real sources found via web research
(2026-07-25). Each URL below was either WebFetch-verified to resolve to the
matching article, or (Angop) confirmed as a live indexed search result on the
official press agency. No URL is fabricated. Event 80 (Banco Sol Cartão
Multicaixa Empresas) had no grounded FILDA-2025 launch article and stays NULL.

Dry run by default; writes only with --apply.
"""
import csv, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data")

# (id, title, url, date, publisher, confidence)
NEW_SOURCES = [
    (122, "FILDA 2025: Leão de Ouro Permanece na Galeria da Sonangol",
     "https://www.sonangol.co.ao/filda-2025-i-leao-de-ouro-permanece-na-galeria-da-sonangol/",
     "2025-07", "Sonangol", "high"),
    (123, "ETU Energias recebe Leão de Ouro na FILDA 2025 e menção honrosa (25 anos)",
     "https://etuenergias.co.ao/noticias/etu-energias-recebe-leao-de-ouro-na-filda-2025-e-mencao-honrosa-pela-sua-trajectoria-de-excellencia-no-sector-petrolifero/178",
     "2025-07", "ETU Energias", "high"),
    (124, "Parceria Estratégica BFA e Mashreq Bank (USD correspondent banking)",
     "https://www.bfa.ao/pt/o-bfa/actualidade/noticias/parceria-estrategica-bfa-e-mashreq-bank/",
     "2025-07-16", "BFA", "high"),
    (125, "Huatong Angola to Invest $900 Million in Barra do Dande Port Terminal",
     "https://360angola.com/business/infrastructure/huatong-angola-to-invest-900-million-in-barra-do-dande-port-terminal/",
     "2026-07-20", "360 Angola", "medium"),
    (126, "Vice-Presidente conhece novas soluções financeiras do BDA na FILDA",
     "https://correiokianda.info/vice-presidente-conhece-novas-solucoes-financeiras-do-bda-na-filda/",
     "2025-07", "Correio da Kianda", "medium"),
    (127, "AEP regressa a Luanda com 15 empresas nacionais (FILDA 2025 delegation)",
     "https://portugalglobal.pt/pt/noticias/2025/julho/aep-regressa-a-luanda-com-15-empresas-nacionais/",
     "2025-07", "Portugal Global/AICEP", "high"),
    (128, "Portuguese banking union finances 66ME water project in Angola's Huíla (Chicomba dam)",
     "https://medafricatimes.com/33963-portuguese-banking-union-finances-66me-water-project-in-angolas-hula.html",
     "2024-07", "Medafrica Times", "medium"),
    (129, "Obras da represa de Chicomba lançadas (construction start, Cuvunji river)",
     "https://angop.ao/noticias/economia/obras-da-represa-de-chicomba-lancadas-no-neste-sabado/",
     "2026-06", "Angop", "high"),
]

# event_id -> new source id
MAPPINGS = {
    75: 122, 76: 122,        # Sonangol 2025 participation + Leão de Ouro
    77: 123, 78: 123,        # ETU 2025 participation (25 yrs) + Leão de Ouro
    81: 126,                 # BDA new financing solutions FILDA 2025
    82: 124, 83: 124,        # BFA-Mashreq partnership announcement + operational
    84: 127, 85: 127,        # AEP Portuguese delegation FILDA 2025 + completion
    88: 125,                 # Huatong $900M Barra do Dande port terminal protocol
    103: 128,                # Chicomba dam financing (presidential decree, BAI Europa+BCP)
    104: 129,                # Chicomba dam construction launched
}
# Event 80 (Banco Sol Cartão Multicaixa Empresas) — no grounded FILDA-2025 launch
# article found; stays NULL.

# ---- load sources ----
src_path = os.path.join(DATA, "sources.csv")
with open(src_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    sfields = reader.fieldnames
    sources = list(reader)

existing_urls = {r["url"] for r in sources if r["url"]}
existing_ids = {int(r["id"]) for r in sources}
to_add = []
for sid, title, url, date, pub, conf in NEW_SOURCES:
    if sid in existing_ids:
        print(f"  [skip] source id {sid} already exists")
        continue
    if url in existing_urls:
        print(f"  [skip] url already present: {url[:70]}")
        continue
    to_add.append({"id": str(sid), "title": title, "url": url, "date": date,
                   "publisher": pub, "archived_url": "", "confidence": conf})

# ---- load events ----
ev_path = os.path.join(DATA, "events.csv")
with open(ev_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    efields = reader.fieldnames
    events = list(reader)

changes = []
for e in events:
    eid = int(e["id"])
    if eid in MAPPINGS:
        old = e["source_id"] or "NULL"
        new = str(MAPPINGS[eid])
        changes.append((eid, e["project_id"], old, new))
        e["source_id"] = new

# ---- report ----
print(f"New sources to add: {len(to_add)}")
for s in to_add:
    print(f"  +{s['id']} [{s['confidence']}] {s['publisher']}")
print(f"\nEvents to ground: {len(changes)} (event 80 stays NULL)")
for eid, pid, old, new in changes:
    print(f"  {eid:>3} {pid:<34} {old:>4} -> {new}")

if "--apply" not in sys.argv:
    print("\n(dry run — nothing written. re-run with --apply)")
    sys.exit(0)

# ---- write sources.csv ----
with open(src_path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=sfields)
    w.writeheader()
    for r in sources:
        w.writerow(r)
    for s in to_add:
        w.writerow(s)

# ---- write events.csv ----
with open(ev_path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=efields)
    w.writeheader()
    for e in events:
        w.writerow(e)

print(f"\n[OK] wrote {len(to_add)} new sources and grounded {len(changes)} events")