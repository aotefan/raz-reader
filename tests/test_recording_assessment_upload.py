"""Exercise the HTTP save acknowledgement independently of the slower score body."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import quote

from engine.recordings import Recordings
from engine.server import ReaderHTTPServer, serve
from tests.test_recordings import sample


class CombinedUploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'manifest.json').write_text(json.dumps({'books': [{
            'book_id': 'book', 'edition_id': 'edition', 'page_count': 2,
            'audio': {'page_map': {}}}]}))
        self.release, self.entered, self.started = (threading.Event() for _ in range(3))
        self.failure = None
        self.calls = 0
        owner = self

        class Server(ReaderHTTPServer):
            def __init__(self, address, handler):
                super().__init__(address, handler)
                owner.server = self
                owner.started.set()

        def assess(_, edition, page, version, text, recordings=None):
            owner.calls += 1
            owner.assertEqual(recordings.read(edition, page)[1], sample())
            owner.entered.set()
            if not owner.release.wait(5):
                raise RuntimeError('Test scoring gate timed out')
            if owner.failure:
                raise RuntimeError(owner.failure)
            result = {'score': 27, 'version': version, 'reference': text, 'words': []}
            if not recordings.save_assessment(edition, page, version, result):
                raise ValueError('录音已更新，请使用最新一次录音。')
            return result

        self.patches = [patch('engine.server.ReaderHTTPServer', Server),
                        patch('engine.server.Assessment.warmup'),
                        patch('engine.server.Assessment.run', assess)]
        for p in self.patches: p.start()
        # Port 0 chooses a free port; the handler's local Host allowlist remains :0.
        self.thread = threading.Thread(target=serve, args=(self.root, 0), daemon=True)
        self.thread.start()
        self.assertTrue(self.started.wait(5))
        self.connections = []

    def tearDown(self):
        self.release.set()
        for connection in self.connections: connection.close()
        self.server.shutdown()
        self.thread.join(5)
        for p in reversed(self.patches): p.stop()
        self.temporary.cleanup()

    def request(self, path, body=b'', headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.connections.append(c)
        c.request('POST', path, body, {'Host': '127.0.0.1:0', **(headers or {})})
        return c.getresponse()

    def reserve(self):
        r = self.request('/api/recordings/edition/1/take')
        return json.loads(r.read())['version']

    def upload(self, version, reference='I plant a little seed.', **headers):
        h = {'Content-Type': 'audio/wav', 'X-Recording-Version': str(version), **headers}
        if reference is not None: h['X-Assessment-Text'] = quote(reference)
        return self.request('/api/recordings/edition/1', sample(), h)

    def test_acknowledges_saved_audio_before_score_then_returns_and_persists_result(self):
        version = self.reserve()
        response = self.upload(version)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader('X-Recording-Saved'), str(version))
        self.assertEqual(response.getheader('Cache-Control'), 'no-store')
        self.assertTrue(self.entered.wait(2))
        store = Recordings(self.root)
        self.assertEqual(store.read('edition', 1)[1], sample())
        self.assertIsNone(store.list('edition')['1']['assessment'])
        prefix = response.read(len(b'{"saved":true,"stale":false,'))
        self.release.set()
        result = json.loads(prefix + response.read())
        self.assertEqual(result['assessment'], store.list('edition')['1']['assessment'])
        self.assertEqual(result['assessment']['score'], 27)

    def test_scoring_failure_keeps_recording_and_reports_separate_retryable_error(self):
        self.failure = '评测暂不可用，请重试。'
        self.release.set()
        result = json.loads(self.upload(self.reserve()).read())
        self.assertTrue(result['saved'])
        self.assertEqual(result['assessment_error'], self.failure)
        self.assertEqual(Recordings(self.root).read('edition', 1)[1], sample())

    def test_legacy_and_stale_uploads_never_start_scoring(self):
        version = self.reserve()
        legacy = self.upload(version, reference=None)
        self.assertIsNone(legacy.getheader('X-Recording-Saved'))
        self.assertEqual(json.loads(legacy.read()), {'saved': True, 'stale': False})
        self.assertEqual(json.loads(self.upload(version).read()), {'saved': False, 'stale': True})
        self.assertEqual(self.calls, 0)

    def test_new_take_rejects_old_score_without_overwriting_latest_audio(self):
        old = self.upload(self.reserve())
        self.assertTrue(self.entered.wait(2))
        latest = self.reserve()
        self.upload(latest, reference=None).read()
        self.release.set()
        self.assertIn('录音已更新', json.loads(old.read())['assessment_error'])
        stored = Recordings(self.root).list('edition')['1']
        self.assertEqual(stored['version'], latest)
        self.assertIsNone(stored['assessment'])

    def test_invalid_reference_preserves_audio_but_foreign_origin_is_rejected(self):
        result = json.loads(self.upload(self.reserve(), reference='<script>bad</script>').read())
        self.assertTrue(result['saved'])
        self.assertIn('assessment_error', result)
        self.assertEqual(self.calls, 0)
        denied = self.upload(self.reserve(), Origin='https://example.com')
        self.assertEqual(denied.status, 403)
        denied.read()
