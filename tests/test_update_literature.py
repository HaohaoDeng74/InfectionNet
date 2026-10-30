import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from scripts import update_literature as u


def record(identifier='1', source='MED', **kwargs):
    return dict(id=identifier, source=source, title='A <i>real</i> paper &amp; data',
                firstPublicationDate='2026-10-24', **kwargs)


def page(hits, records, cursor=None):
    result = {'hitCount': hits, 'resultList': {'result': records}}
    if cursor:
        result['nextCursorMark'] = cursor
    return result


class UpdaterTests(unittest.TestCase):
    def test_date_boundaries_and_invalid_dates(self):
        self.assertEqual(u.date_range(7, dt.date(2026, 10, 30)), 'FIRST_PDATE:[2026-10-24 TO 2026-10-30]')
        for value in ('2026', '2026-02-30', '', None, '2026-10-30T12:00:00Z'):
            self.assertEqual(u.publication_date(value), '')
        self.assertEqual(u.publication_date('2024-02-29'), '2024-02-29')

    def test_normalize_core_fields_dates_and_html(self):
        p = u.normalize(record(pubYear='2027', journalInfo={'journal': {'title': 'Cell'}}), {'week', 'sepsis'})
        self.assertEqual(p['title'], 'A real paper & data')
        self.assertEqual(u.plain_text('Trem2&lt;sup&gt;+&lt;/sup&gt;'), 'Trem2+')
        self.assertEqual(p['journalTitle'], 'Cell')
        self.assertEqual(p['pubYear'], '2027')  # Print year must not become first publication date.
        self.assertEqual(p['firstPublicationDate'], '2026-10-24')
        self.assertEqual(p['topics'], ['sepsis'])
        p = u.normalize({'id': '2', 'source': 'MED', 'title': 'No date', 'pubYear': '2026'})
        self.assertEqual(p['firstPublicationDate'], '')
        with self.assertRaises(ValueError):
            u.normalize({'id': '2', 'source': 'MED', 'title': 'Bad date', 'firstPublicationDate': '2026-02-30'})
        with self.assertRaises(ValueError):
            u.normalize({'id': '2', 'source': 'MED'})

    def test_pagination_consumes_multiple_pages_and_reports_cap(self):
        with patch.object(u, 'PAGE_SIZE', 2), patch.object(u, 'request_page', side_effect=[page(5, [record('1'), record('2')], 'next'), page(5, [record('3')], 'last')]) as request:
            rows, stats = u.request_query('sepsis', 'query', limit=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(request.call_args_list[1].args, ('query', 'next', 1))
        self.assertEqual(stats['hit_count'], 5)
        self.assertTrue(stats['truncated'])
        with patch.object(u, 'request_page', side_effect=[page(2, [record('1')], 'next'), page(2, [record('2')])]):
            self.assertFalse(u.request_query('topic', 'q')[1]['truncated'])

    def test_repeated_missing_cursor_and_duplicate_pages_fail(self):
        for responses in ([page(2, [record('1')])],
                          [page(2, [record('1')], '*')],
                          [page(2, [record('1')], 'n'), page(2, [record('1')])],
                          [page(2, [record('1')], 'n'), page(3, [record('2')])]):
            with self.subTest(responses=responses), patch.object(u, 'request_page', side_effect=responses), self.assertRaises(ValueError):
                u.request_query('topic', 'q')

    def test_malformed_or_error_payload_is_not_empty_success(self):
        for payload in ({}, {'hitCount': 0}, {'hitCount': '0', 'resultList': {'result': []}},
                        {'hitCount': 0, 'errMsg': 'bad query', 'resultList': {'result': []}},
                        {'hitCount': 1, 'resultList': {'result': ['bad']}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                u.validate_page(payload)
        self.assertEqual(u.validate_page(page(0, [])), (0, []))

    def test_retry_transient_and_reject_permanent_errors(self):
        ok = json.dumps(page(1, [record()])).encode()
        for error in (urllib.error.URLError('offline'), TimeoutError(),
                      urllib.error.HTTPError(u.API, 429, 'rate limited', {'Retry-After': '1'}, None),
                      urllib.error.HTTPError(u.API, 503, 'unavailable', {}, None)):
            with patch.object(u.urllib.request, 'urlopen', side_effect=[error, io.BytesIO(ok)]) as open_url, patch.object(u.time, 'sleep'):
                self.assertEqual(u.request_page('q')['hitCount'], 1)
                self.assertEqual(open_url.call_count, 2)
        with patch.object(u.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError(u.API, 400, 'bad query', {}, None)) as open_url, self.assertRaises(urllib.error.HTTPError):
            u.request_page('q')
        self.assertEqual(open_url.call_count, 1)
        with patch.object(u.urllib.request, 'urlopen', side_effect=TimeoutError()) as open_url, patch.object(u.time, 'sleep'), self.assertRaises(TimeoutError):
            u.request_page('q')
        self.assertEqual(open_url.call_count, 3)
        for bad in (b'not json', b'{}'):
            with patch.object(u.urllib.request, 'urlopen', side_effect=[io.BytesIO(bad) for _ in range(3)]) as open_url, patch.object(u.time, 'sleep'), self.assertRaises(ValueError):
                u.request_page('q')
            self.assertEqual(open_url.call_count, 3)

    def test_transitive_dedup_union_topics_and_deterministic_metadata(self):
        med = u.normalize(record('1', doi='https://doi.org/10.1234/ABC'), {'sepsis'})
        pmc = u.normalize(record('PMC2', 'PMC'), {'microbial_single_cell'})
        bridge = u.normalize(record('PMC3', 'PMC', pmid='1', pmcid='PMC2'), {'infection_omics'})
        bridge['analysis'] = {'finding': 'curated'}
        rows = u.merge_records([pmc, med, bridge])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['doi'], '10.1234/abc')
        self.assertEqual(rows[0]['topics'], ['infection_omics', 'microbial_single_cell', 'sepsis'])
        self.assertEqual(rows[0]['source'], 'MED')
        self.assertEqual(rows[0]['analysis'], {'finding': 'curated'})
        self.assertEqual(rows, u.merge_records([bridge, med, pmc]))

    def test_doi_dedup_and_source_namespace_collision(self):
        med = u.normalize(record('1', doi='10.1234/AbC'))
        pmc = u.normalize(record('PMC2', 'PMC', doi='doi:10.1234/abc'))
        self.assertEqual(len(u.merge_records([med, pmc])), 1)
        self.assertEqual(len(u.merge_records([u.normalize(record('1')), u.normalize(record('1', 'PPR'))])), 2)
        # A preprint with a distinct DOI remains a separate version.
        preprint = u.normalize(record('PPR2', 'PPR', doi='10.1234/preprint'))
        self.assertEqual(len(u.merge_records([med, preprint])), 2)

    def test_manual_annotations_survive_but_api_metadata_wins(self):
        manual = [{'pmid': '1', 'doi': '10.1234/abc', 'title': 'Handwritten title', 'topics': ['sepsis', 'week'], 'analysis': {'finding': 'curated'}}]
        raw = record('1', doi='10.1234/abc', abstractText='Database abstract')
        stat = {'hit_count': 1, 'fetched': 1, 'truncated': False, 'limit': 500}
        with patch.object(u, 'request_query', return_value=([raw], stat)):
            snapshot = u.build_snapshot(manual)
        self.assertEqual(snapshot['record_count'], 1)
        self.assertEqual(snapshot['papers'][0]['analysis'], manual[0]['analysis'])
        self.assertEqual(snapshot['papers'][0]['abstractText'], 'Database abstract')
        self.assertNotEqual(snapshot['papers'][0]['title'], 'Handwritten title')
        self.assertNotIn('week', snapshot['papers'][0]['topics'])
        with patch.object(u, 'request_query', return_value=([record('1', doi='10.1234/wrong')], stat)), self.assertRaisesRegex(ValueError, 'DOI disagrees'):
            u.build_snapshot(manual)

    def test_empty_or_failed_refresh_preserves_old_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'papers.json'
            manual = Path(directory) / 'manual.json'
            output.write_bytes(b'old snapshot')
            manual.write_text('[]')
            for response in (ValueError('invalid API'), ([], {'hit_count': 0})):
                kwargs = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch.object(u, 'OUTPUT', output), patch.object(u, 'MANUAL', manual), patch.object(u, 'request_query', **kwargs), self.assertRaises(ValueError):
                    u.main()
                self.assertEqual(output.read_bytes(), b'old snapshot')

    def test_atomic_replace_failure_and_success(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'papers.json'
            output.write_bytes(b'old')
            with patch.object(u.os, 'replace', side_effect=OSError('disk error')), self.assertRaises(OSError):
                u.atomic_write({'papers': []}, output)
            self.assertEqual(output.read_bytes(), b'old')
            self.assertEqual(list(Path(directory).iterdir()), [output])
            u.atomic_write({'papers': []}, output)
            self.assertEqual(json.loads(output.read_text()), {'papers': []})

    def test_committed_snapshot_invariants(self):
        payload = json.loads(u.OUTPUT.read_text())
        self.assertEqual(payload['record_count'], len(payload['papers']))
        self.assertEqual(len({p['id'] for p in payload['papers']}), payload['record_count'])
        self.assertEqual(len(u.merge_records(payload['papers'])), payload['record_count'])
        for paper in payload['papers']:
            self.assertNotIn('week', paper['topics'])
            self.assertEqual(u.publication_date(paper['firstPublicationDate']), paper['firstPublicationDate'])


if __name__ == '__main__':
    unittest.main()
