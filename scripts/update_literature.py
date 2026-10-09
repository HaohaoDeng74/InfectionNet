#!/usr/bin/env python3
"""Refresh a small static InfectionNet snapshot from the Europe PMC API."""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
OUTPUT = ROOT / "data" / "papers.json"
MANUAL = ROOT / "data" / "manual_records.json"
MAX_PER_QUERY = 100
TOPIC_QUERIES = {
    "infection_omics": 'TITLE_ABS:(infection OR infectious OR pathogen OR host-pathogen) AND TITLE_ABS:("single-cell" OR "single cell" OR spatial OR multi-omics OR multiomics OR transcriptom*)',
    "sepsis": 'TITLE_ABS:(sepsis OR "septic shock") AND TITLE_ABS:(immune OR immun* OR "single-cell" OR "single cell" OR spatial OR multi-omics OR transcriptom*)',
    "microbial_single_cell": 'TITLE_ABS:(bacter* OR microb* OR fungus OR fungal OR yeast OR pathogen*) AND TITLE_ABS:("single-cell" OR "single cell" OR "single-cell RNA" OR scRNA-seq)',
}


def date_range(days: int) -> str:
    today = dt.datetime.now(dt.timezone.utc).date()
    return f"FIRST_PDATE:[{today - dt.timedelta(days=days)} TO {today}]"


def request_query(name: str, query: str) -> tuple[str, list[dict]]:
    params = urllib.parse.urlencode({"query": query + " sort_date:y", "format": "json", "resultType": "core", "pageSize": MAX_PER_QUERY})
    req = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": "InfectionNet/0.1 literature discovery", "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                payload = json.load(response)
            return name, payload.get("resultList", {}).get("result", [])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    return name, []


def normalize(item: dict, topics: set[str]) -> dict | None:
    identifier = str(item.get("pmid") or item.get("id") or item.get("doi") or "").strip()
    title = " ".join(str(item.get("title") or "").split())
    if not identifier or not title:
        return None
    pmid = item.get("pmid") or (identifier if item.get("source") == "MED" else "")
    doi, pmcid, source = (str(item.get(k) or "").strip() for k in ("doi", "pmcid", "source"))
    article_id = str(item.get("id") or identifier)
    journal = item.get("journalTitle") or item.get("journalInfo", {}).get("journal", {}).get("title") or ""
    return {
        "id": str(pmid or pmcid or doi or article_id), "pmid": str(pmid) if pmid else "", "pmcid": pmcid,
        "doi": doi, "title": title, "authorString": item.get("authorString") or "", "journalTitle": journal,
        "pubYear": str(item.get("pubYear") or ""), "firstPublicationDate": item.get("firstPublicationDate") or "",
        "abstractText": " ".join(str(item.get("abstractText") or "").split()), "isOpenAccess": item.get("isOpenAccess") or "N",
        "source": source, "topics": sorted(topics),
        "sourceUrl": f"https://europepmc.org/article/{source}/{urllib.parse.quote(article_id)}" if source else "https://europepmc.org/",
    }


def main() -> int:
    queries = {name: f"({query}) AND {date_range(730)}" for name, query in TOPIC_QUERIES.items()}
    queries["week"] = f'({TOPIC_QUERIES["infection_omics"]}) AND {date_range(7)}'
    collected: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(request_query, topic, query) for topic, query in queries.items()]
        for future in as_completed(futures):
            topic, records = future.result()
            for raw in records:
                paper = normalize(raw, {topic})
                if paper is None:
                    continue
                key = paper["id"]
                if key not in collected:
                    collected[key] = paper
                else:
                    current = collected[key]
                    current["topics"] = sorted(set(current["topics"]) | set(paper["topics"]))
                    for field in ("abstractText", "pmcid", "doi", "pmid", "authorString", "journalTitle"):
                        if not current.get(field) and paper.get(field):
                            current[field] = paper[field]

    # Keep hand-checked records and explanations separate from the API snapshot.
    manual = json.loads(MANUAL.read_text(encoding="utf-8")) if MANUAL.exists() else []
    for record in manual:
        key = str(record["id"])
        if key in collected:
            merged = {**record, **collected[key]}
            merged["topics"] = sorted(set(record.get("topics", [])) | set(collected[key].get("topics", [])))
            merged["analysis"] = record.get("analysis", {})
            collected[key] = merged
        else:
            collected[key] = record

    papers = sorted(collected.values(), key=lambda p: (p.get("firstPublicationDate", ""), p.get("title", "")), reverse=True)
    payload = {"schema_version": 1, "updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
               "source": "Europe PMC REST API; queries in scripts/update_literature.py", "record_count": len(papers), "papers": papers}
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(papers)} records to {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Literature refresh failed: {exc}", file=sys.stderr)
        sys.exit(1)
