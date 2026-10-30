#!/usr/bin/env python3
"""Build a bounded, validated snapshot; never replace a good file on failure."""
from __future__ import annotations

import datetime as dt
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
OUTPUT = ROOT / 'data' / 'papers.json'
MANUAL = ROOT / 'data' / 'manual_records.json'
PAGE_SIZE = 100
MAX_PER_QUERY = 500  # Deliberately bounded discovery snapshot, not an exhaustive database.
SOURCES = '(SRC:MED OR SRC:PMC OR SRC:PPR)'
RNA = '(RNA-seq OR "RNA sequencing" OR transcriptom*)'
MICROBIAL_PHRASES = '("bacterial single-cell" OR "bacterial single cell" OR "microbial single-cell" OR "microbial single cell" OR "fungal single-cell" OR "fungal single cell" OR "yeast single-cell" OR "yeast single cell" OR "single bacterial cells" OR "single yeast cells" OR "single fungal cells" OR "single cells of bacteria" OR "single-bacterium" OR "single-cell bacterial" OR "single-cell microbial" OR "single-cell fungal" OR "single-cell yeast" OR "single microbe" OR "single-microbe" OR "single-cell RNA sequencing of bacteria" OR "single-cell transcriptomics of bacteria")'
TOPIC_QUERIES = {
    'infection_omics': 'TITLE_ABS:(infection OR infectious OR pathogen OR host-pathogen) AND TITLE_ABS:("single-cell" OR "single cell" OR spatial OR multi-omics OR multiomics OR transcriptom*)',
    'sepsis': 'TITLE_ABS:(sepsis OR "septic shock") AND TITLE_ABS:(immun* OR "single-cell" OR "single cell" OR spatial OR multi-omics OR transcriptom*)',
    'microbial_single_cell': f'((TITLE_ABS:{MICROBIAL_PHRASES} AND TITLE_ABS:{RNA}) OR TITLE_ABS:(PETRI-seq OR microSPLiT OR BacDrop OR ProBac-seq OR smRandom-seq))',
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    parser = TextParser()
    parser.feed(unescape(str(value or '')))
    return ' '.join(''.join(parser.parts).split())


def publication_date(value):
    """Only exact valid dates; do not invent dates from a year or print date."""
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return ''
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return ''


def date_range(days, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return f'FIRST_PDATE:[{today - dt.timedelta(days=days - 1)} TO {today}]'


def canonical_doi(value):
    return re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', str(value or '').strip(), flags=re.I).lower()


def aliases(paper):
    keys = set()
    for name in ('pmid', 'pmcid', 'doi'):
        value = str(paper.get(name) or '').strip()
        value = canonical_doi(value) if name == 'doi' else value.upper()
        if value:
            keys.add(f'{name}:{value}')
    if paper.get('source') and paper.get('sourceId'):
        keys.add(f"source:{paper['source']}:{paper['sourceId']}")
    return keys


def validate_page(payload):
    if not isinstance(payload, dict) or payload.get('errMsg') or payload.get('error'):
        raise ValueError('Europe PMC returned an error or non-object payload')
    hits = payload.get('hitCount')
    records = payload.get('resultList', {}).get('result') if isinstance(payload.get('resultList'), dict) else None
    if type(hits) is not int or hits < 0 or not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise ValueError('Europe PMC payload is missing a valid hitCount/resultList.result')
    return hits, records


def request_page(query, cursor='*', page_size=PAGE_SIZE):
    params = urllib.parse.urlencode(dict(query=query + ' sort_date:y', format='json', resultType='core', pageSize=page_size, cursorMark=cursor, synonym='false'))
    req = urllib.request.Request(f'{API}?{params}', headers={'User-Agent': 'InfectionNet/0.2 literature discovery', 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.load(response)
            validate_page(payload)
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429) and not 500 <= exc.code < 600:
                raise
            if attempt == 2:
                raise
            retry_after = exc.headers.get('Retry-After', '') if exc.headers else ''
            delay = min(int(retry_after), 30) if retry_after.isdigit() else 2 ** attempt
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            if attempt == 2:
                raise
            delay = 2 ** attempt
        time.sleep(delay)
    raise RuntimeError('Unreachable retry state')


def request_query(name, query, limit=MAX_PER_QUERY):
    records, cursor, seen_cursors, seen_ids = [], '*', {'*'}, set()
    hits = None
    while True:
        payload = request_page(query, cursor, min(PAGE_SIZE, limit - len(records)))
        page_hits, batch = validate_page(payload)
        if hits is None:
            hits = page_hits
        elif hits != page_hits:
            raise ValueError(f'{name}: hitCount changed while paging; retry the refresh')
        for item in batch:
            key = (item.get('source'), item.get('id'))
            if key in seen_ids:
                raise ValueError(f'{name}: duplicate record across pages')
            seen_ids.add(key)
        records.extend(batch)
        target = min(hits, limit)
        if len(records) > target:
            raise ValueError(f'{name}: returned more records than requested')
        if len(records) == target:
            break
        next_cursor = payload.get('nextCursorMark')
        if not batch or not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
            raise ValueError(f'{name}: pagination stopped before the advertised hitCount')
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return records, {'query': query, 'hit_count': hits, 'fetched': len(records), 'truncated': hits > len(records), 'limit': limit}


def normalize(item, topics=()):
    source = str(item.get('source') or '').strip().upper()
    source_id = str(item.get('id') or '').strip()
    title = plain_text(item.get('title'))
    if source not in ('MED', 'PMC', 'PPR') or not source_id or not title:
        raise ValueError('Record lacks a supported source, source ID, or title')
    pmid = str(item.get('pmid') or (source_id if source == 'MED' else '')).strip()
    pmcid = str(item.get('pmcid') or (source_id if source == 'PMC' else '')).strip().upper()
    doi = canonical_doi(item.get('doi'))
    if pmid and not pmid.isdigit():
        raise ValueError(f'Invalid PMID: {pmid}')
    if pmcid and not re.fullmatch(r'PMC\d+', pmcid):
        raise ValueError(f'Invalid PMCID: {pmcid}')
    if doi and not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
        raise ValueError(f'Invalid DOI: {doi}')
    raw_date = item.get('firstPublicationDate')
    first_date = publication_date(raw_date)
    if raw_date and not first_date:
        raise ValueError(f'Invalid firstPublicationDate for {source}:{source_id}: {raw_date}')
    journal_info = item.get('journalInfo') or {}
    return {
        'id': pmid or pmcid or f'{source}:{source_id}', 'pmid': pmid, 'pmcid': pmcid, 'doi': doi,
        'title': title, 'authorString': plain_text(item.get('authorString')),
        'journalTitle': plain_text(item.get('journalTitle') or journal_info.get('journal', {}).get('title')),
        'pubYear': str(item.get('pubYear') or journal_info.get('yearOfPublication') or ''),
        'firstPublicationDate': first_date, 'abstractText': plain_text(item.get('abstractText')),
        'isOpenAccess': item.get('isOpenAccess') or 'N', 'source': source, 'sourceId': source_id,
        'topics': sorted(set(topics) - {'week'}),
        'sourceUrl': f'https://europepmc.org/article/{source}/{urllib.parse.quote(source_id, safe="")}',
    }


def merge_records(records):
    """Union all identifier aliases, including transitive MED/PMC matches."""
    groups, index = {}, {}
    for paper in records:
        keys = aliases(paper)
        matching = {index[k] for k in keys if k in index}
        group = min(matching) if matching else len(groups)
        if not matching:
            # IDs are monotonic even after groups have been merged/deleted.
            group = max(groups, default=-1) + 1
            groups[group] = []
        for other in matching - {group}:
            groups[group].extend(groups.pop(other))
            for key, value in list(index.items()):
                if value == other:
                    index[key] = group
        groups[group].append(paper)
        for key in keys:
            index[key] = group
    merged = []
    rank = {'MED': 0, 'PMC': 1, 'PPR': 2}
    for group in groups.values():
        # Prefer PubMed metadata over a duplicate PMC record, independently of arrival order.
        group.sort(key=lambda p: (rank[p['source']], p['sourceId']))
        current = dict(group[0])
        current['topics'] = sorted({t for p in group for t in p['topics']} - {'week'})
        for paper in group[1:]:
            for field in ('pmid', 'pmcid', 'doi', 'abstractText', 'authorString', 'journalTitle', 'pubYear', 'firstPublicationDate'):
                if not current.get(field):
                    current[field] = paper.get(field, '')
        current['id'] = current['pmid'] or current['pmcid'] or current['id']
        analyses = [p['analysis'] for p in group if p.get('analysis')]
        if analyses:
            current['analysis'] = analyses[0]
        merged.append(current)
    ids = [p['id'] for p in merged]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate canonical record IDs')
    return merged


def build_snapshot(manual, today=None):
    today = today or dt.datetime.now(dt.timezone.utc).date()
    records, stats = [], {}
    for topic, query in TOPIC_QUERIES.items():
        query = f'({query}) AND {SOURCES} AND {date_range(730, today)}'
        raw, stats[topic] = request_query(topic, query)
        if not raw:
            raise ValueError(f'{topic}: unexpected empty discovery result; keeping previous snapshot')
        records.extend(normalize(item, {topic}) for item in raw)
        print(f'{topic}: {len(raw)}/{stats[topic]["hit_count"]} records (truncated={stats[topic]["truncated"]})', flush=True)
    # Pinned older papers must be looked up again, not silently copied from handwritten metadata.
    for entry in manual:
        if entry.get('pmid') and str(entry['pmid']).isdigit():
            query = f'EXT_ID:{entry["pmid"]} AND SRC:MED'
        elif canonical_doi(entry.get('doi')):
            query = f'DOI:"{canonical_doi(entry["doi"])}" AND {SOURCES}'
        else:
            raise ValueError('Pinned record needs a PMID or DOI')
        raw, stat = request_query('pinned', query)
        if not raw or stat['truncated']:
            raise ValueError(f'Pinned record lookup failed: {query}')
        candidates = [normalize(item, entry.get('topics', [])) for item in raw]
        if not any(aliases(entry) & aliases(p) for p in candidates):
            raise ValueError(f'Pinned identifiers did not match: {query}')
        expected_doi = canonical_doi(entry.get('doi'))
        if expected_doi and not any(p['doi'] == expected_doi for p in candidates):
            raise ValueError(f'Pinned DOI disagrees with Europe PMC: {query}')
        for paper in candidates:
            paper['analysis'] = entry.get('analysis', {})
        records.extend(candidates)
    papers = sorted(merge_records(records), key=lambda p: (p['firstPublicationDate'], p['title']), reverse=True)
    return {'schema_version': 1, 'updated_at': dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z'),
            'source': 'Europe PMC REST API; bounded discovery snapshot; queries below',
            'query_stats': stats, 'record_count': len(papers), 'papers': papers}


def atomic_write(payload, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent, prefix='.papers-', suffix='.tmp', delete=False) as file:
            temp_name = file.name
            file.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, output)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def main():
    manual = json.loads(MANUAL.read_text(encoding='utf-8'))
    if not isinstance(manual, list):
        raise ValueError('manual_records.json must contain a list')
    payload = build_snapshot(manual)
    atomic_write(payload, OUTPUT)
    print(f'Wrote {payload["record_count"]} records to {OUTPUT.name}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(f'Literature refresh failed; previous snapshot retained: {exc}', file=sys.stderr)
        sys.exit(1)
