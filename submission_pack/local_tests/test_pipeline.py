"""Offline fixtures exercise scientific cutoff rules and the execution contract."""
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from http.server import HTTPServer

PACK = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / 'fixtures'
sys.path.insert(0, str(PACK))
import ace_data as data
import model

T0 = data.timestamp('20270101T060000Z')
START = data.timestamp('20261201T000000Z')
JAN = data.timestamp('20270101T000000Z')


def fixture_open(url, timeout=20):
    return io.BytesIO((FIXTURES / url.rsplit('/', 1)[1]).read_bytes())


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    def collect(self, opener=fixture_open, t0=T0, start=START):
        with patch.object(data.urllib.request, 'urlopen', side_effect=opener):
            return data.collect(start, t0 + data.HOUR, self.out, cutoff=t0)

    def test_last_valid_value_and_causal_fill(self):
        prepared = self.collect()
        self.assertEqual(model.predict(T0, prepared), [477.2] * 72)
        self.assertEqual(prepared['observation_utc'], '2027-01-01T03:00:00Z')
        with (self.out / 'hourly.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]['filled_speed_kms'], '')
        self.assertEqual(rows[-1]['filled_speed_kms'], '477.2')
        self.assertEqual(rows[-1]['last_observation_utc'], prepared['observation_utc'])
        self.assertEqual(rows[-1]['was_missing'], '1')
        self.assertEqual(rows[-4]['status'], '6')
        meta = json.loads((self.out / 'metadata.json').read_text())
        self.assertEqual(len(meta['sources']), 2)
        self.assertEqual(len(meta['sources'][0]['sha256']), 64)

    def test_publication_cutoff_and_missing_or_invalid_issue(self):
        original = (FIXTURES / '202701_ace_swepam_1h.txt').read_bytes()
        for issue in [b':Issued: 2027 Jan 01 0601 UT', b':Issued: bad', b'# no issue']:
            with self.subTest(issue=issue):
                def opener(url, timeout=20):
                    if '202701_' in url:
                        return io.BytesIO(original.replace(b':Issued: 2027 Jan 01 0550 UT', issue))
                    return fixture_open(url)
                prepared = self.collect(opener)
                self.assertEqual(prepared['speed_kms'], 415.0)
                self.assertEqual(prepared['observation_utc'], '2026-12-31T23:00:00Z')

    def test_exact_cutoffs_are_inclusive(self):
        raw = b':Issued: 2027 Jan 01 0600 UT\n2027 01 01 0600 61406 21600 0 1 500 100000\n'
        prepared = self.collect(lambda *_args, **_kw: io.BytesIO(raw), start=JAN)
        self.assertEqual(prepared['speed_kms'], 500)
        self.assertEqual(model.predict(T0, prepared), [500] * 72)

    def test_staleness_counts_observation_not_forward_fill(self):
        prepared = self.collect()
        boundary = data.timestamp(prepared['observation_utc']) + data.MAX_AGE
        prepared['t0'] = data.iso(boundary)
        self.assertEqual(model.predict(boundary, prepared), [477.2] * 72)
        prepared['t0'] = data.iso(boundary + data.HOUR)
        with self.assertRaisesRegex(ValueError, 'older'):
            model.predict(boundary + data.HOUR, prepared)
        with self.assertRaisesRegex(ValueError, '72 hours'):
            self.collect(t0=data.timestamp('20270105T060000Z'))
        self.assertFalse((self.out / 'input.json').exists())

    def test_no_valid_data_fails(self):
        raw = b':Issued: 2027 Jan 01 0550 UT\n2027 01 01 0000 61406 0 0 1 -9999.9 1\n'
        with self.assertRaisesRegex(ValueError, 'No eligible'):
            self.collect(lambda *_a, **_k: io.BytesIO(raw), start=JAN)

    def test_invalid_archives(self):
        row = b'2027 01 01 0000 61406 0 0 1 400 1\n'
        for raw in [b'<html>error</html>', b'2027 01\n', row + row,
                    row.replace(b'0000', b'0015'), row.replace(b'2027 01', b'2026 12')]:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                data.parse_archive(raw, JAN)

    def test_retries_and_timeout(self):
        raw = (FIXTURES / '202701_ace_swepam_1h.txt').read_bytes()
        with patch.object(data.urllib.request, 'urlopen', side_effect=[URLError('offline'), io.BytesIO(raw)]) as fetch, patch.object(data.time, 'sleep'):
            data.download(JAN, self.out)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.kwargs['timeout'], 20)
        with patch.object(data.urllib.request, 'urlopen', side_effect=URLError('offline')) as fetch, patch.object(data.time, 'sleep'):
            with self.assertRaises(URLError):
                data.download(JAN, self.out)
            self.assertEqual(fetch.call_count, 3)

    def test_month_boundaries_and_timestamp_validation(self):
        self.assertEqual([m.strftime('%Y%m') for m in data.months_between(START, T0)], ['202612', '202701'])
        self.assertEqual(list(data.months_between(START, JAN)), [START])
        for value in ['2027-01-01T00:00:00', '20270101T000100Z']:
            with self.assertRaises(ValueError):
                data.timestamp(value)

    def test_model_rejects_invalid_prepared_inputs(self):
        good = self.collect()
        for key, value in [('t0', '20270101T070000Z'), ('observation_utc', '20270101T070000Z'),
                           ('issued_at_utc', '2027-01-01T06:01:00Z'), ('speed_kms', -1),
                           ('speed_kms', float('inf'))]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                model.predict(T0, {**good, key: value})

    def test_archive_mode_is_explicitly_not_publication_safe(self):
        with patch.object(data.urllib.request, 'urlopen', side_effect=fixture_open):
            data.collect(JAN, T0 + data.HOUR, self.out)
        self.assertEqual(json.loads((self.out / 'metadata.json').read_text())['mode'], 'archive')
        self.assertFalse((self.out / 'input.json').exists())

    def process_env(self, bad=False):
        # Inject fixtures in child Python processes without a production bypass flag.
        (self.out / 'sitecustomize.py').write_text(
            'import io, urllib.request\nfrom pathlib import Path\n'
            f'root = Path({str(FIXTURES)!r})\n'
            'def fixture(url, timeout=20):\n'
            '    raw = (root / url.rsplit("/", 1)[1]).read_bytes()\n'
            + ('    raw = raw.replace(b":Issued:", b"#Issued:")\n' if bad else '')
            + '    return io.BytesIO(raw)\n'
            'urllib.request.urlopen = fixture\n')
        return {**os.environ, 'PYTHONPATH': str(self.out)}

    def test_entrypoint_failure_never_invokes_upload(self):
        env = self.process_env(bad=True)
        env['OUTPUT_POST'] = 'invalid value that must never be read'
        result = subprocess.run(['bash', str(PACK / 'run.sh'), '20270101T060000Z'],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No eligible', result.stderr)
        self.assertNotIn('[run] produced', result.stdout)
        self.assertNotIn('[upload]', result.stdout)
        self.assertNotIn('jq', result.stderr)

    @unittest.skipUnless(shutil.which('jq') and shutil.which('curl'), 'upload helper requires jq and curl')
    def test_entrypoint_through_existing_mock_upload(self):
        spec = importlib.util.spec_from_file_location('mock_platform', PACK / 'local_tests/mock_platform.py')
        mock = importlib.util.module_from_spec(spec)
        # The existing mock creates a relative out directory at import time.
        cwd = os.getcwd()
        try:
            os.chdir(self.out)
            spec.loader.exec_module(mock)
        finally:
            os.chdir(cwd)
        mock.OUT = self.out / 'received'
        mock.OUT.mkdir()
        server = HTTPServer(('127.0.0.1', 0), mock.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            env = self.process_env()
            env['OUTPUT_POST'] = json.dumps({'url': f'http://127.0.0.1:{server.server_port}/', 'fields': {'key': 'local'}})
            result = subprocess.run(['bash', str(PACK / 'run.sh'), '20270101T060000Z'],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            forecast = json.loads((mock.OUT / 'forecast.json').read_text())
            self.assertEqual(forecast['forecast_speed_kms'], [477.2] * 72)
            self.assertEqual(forecast['valid_from'], '2027-01-01T07:00:00Z')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
