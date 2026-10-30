#!/usr/bin/env python3
"""Read-only network smoke test: identifiers, field mapping, and topic recall."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import update_literature as u


def check():
    expected = {
        '36708705': ('10.1016/j.cell.2023.01.002', '2023-01-27', 'microbial_single_cell', 'Bacterial droplet-based'),
        '41339491': ('10.1038/s41590-025-02345-x', '2025-12-03', 'sepsis', 'Single-cell multi-omic landscape'),
    }
    for pmid, (doi, first_date, topic, title) in expected.items():
        query = f'EXT_ID:{pmid} AND SRC:MED'
        raw, stat = u.request_query('known-paper', query)
        assert stat['hit_count'] == 1 and len(raw) == 1, (pmid, stat)
        paper = u.normalize(raw[0])
        assert paper['pmid'] == pmid and paper['doi'] == doi, paper
        assert paper['firstPublicationDate'] == first_date, paper
        assert paper['title'].startswith(title), paper['title']
        assert paper['abstractText'] and paper['journalTitle'], paper
        raw, stat = u.request_query('topic-recall', f'({u.TOPIC_QUERIES[topic]}) AND ({query})')
        assert len(raw) == 1, (pmid, topic, stat)
        print(json.dumps({'pmid': pmid, 'doi': doi, 'firstPublicationDate': first_date, 'pubYear': paper['pubYear'], 'topic_match': topic}, ensure_ascii=False), flush=True)
    raw, stat = u.request_query('infection-recall', f'({u.TOPIC_QUERIES["infection_omics"]}) AND EXT_ID:41339491 AND SRC:MED')
    assert len(raw) == 1, stat
    # Regression examples returned by the original broad microbial query.
    for pmid in ('42957764', '42956749', '42900335'):
        raw, stat = u.request_query('microbial-negative', f'({u.TOPIC_QUERIES["microbial_single_cell"]}) AND EXT_ID:{pmid} AND SRC:MED')
        assert not raw, (pmid, stat)
    # An old BacDrop paper must not be falsely returned by a recent FIRST_PDATE window.
    raw, stat = u.request_query('date-exclusion', f'EXT_ID:36708705 AND SRC:MED AND {u.date_range(7)}')
    assert not raw and stat['hit_count'] == 0, stat
    for topic, query in u.TOPIC_QUERIES.items():
        raw, stat = u.request_query(topic, f'({query}) AND {u.SOURCES} AND {u.date_range(730)}', limit=3)
        assert raw, (topic, stat)
        for item in raw:
            paper = u.normalize(item, {topic})
            assert paper['sourceUrl'].startswith('https://europepmc.org/article/')
        print(json.dumps({'topic': topic, **stat, 'sample_ids': [r['id'] for r in raw], 'sample_titles': [u.plain_text(r.get('title')) for r in raw]}, ensure_ascii=False), flush=True)
    print('PASS: live identifiers, metadata, topic recall, date exclusion and recent-query samples')


if __name__ == '__main__':
    check()
