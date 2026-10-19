import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.cloud_sessions import CloudSessions, COOKIE
from engine.recordings import Recordings
from tests.test_recordings import sample


class CloudSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sessions = CloudSessions(self.root, 'https://reader.example.com')

    def test_cookie_is_signed_persistent_and_cannot_select_another_directory(self):
        directory, cookie = self.sessions.resolve(None, create=True)
        self.assertIn('HttpOnly; Secure; SameSite=Strict', cookie)
        self.assertEqual(CloudSessions(self.root, 'https://reader.example.com').resolve(cookie), (directory, None))
        for bad in (f'{COOKIE}=../../owner', f'{COOKIE}={directory.name}.fake', None):
            self.assertEqual(self.sessions.resolve(bad), (None, None))
        other, _ = self.sessions.resolve(None, create=True)
        self.assertNotEqual(directory, other)
        self.assertEqual((self.root / 'session.key').stat().st_mode & 0o777, 0o600)

    def test_same_book_page_version_and_score_are_isolated(self):
        first, _ = self.sessions.resolve(None, create=True)
        second, _ = self.sessions.resolve(None, create=True)
        a, b = Recordings(first), Recordings(second)
        va = a.reserve('book', 2)
        vb = b.reserve('book', 2)
        self.assertEqual(va, vb)
        self.assertTrue(self.sessions.save(a, 'book', 2, va, 'audio/wav', sample()))
        a.save_assessment('book', 2, va, {'score': 91, 'version': va})
        self.assertIsNone(b.read('book', 2))
        self.assertEqual(b.list('book'), {})
        self.assertEqual(b.summaries({'book': 2}), {})
        self.assertEqual(a.summaries({'book': 2})['book']['average'], 91)

    def test_global_quota_rejects_new_data_and_keeps_existing_recording(self):
        directory, _ = self.sessions.resolve(None, create=True)
        store = Recordings(directory)
        version = store.reserve('book', 1)
        self.sessions.save(store, 'book', 1, version, 'audio/wav', sample())
        version = store.reserve('book', 1)
        with patch.object(self.sessions, 'disk_usage', return_value=2 * 1024**3):
            with self.assertRaises(ValueError):
                self.sessions.save(store, 'book', 1, version, 'audio/wav', sample())
        self.assertEqual(store.list('book')['1']['version'], version - 1)

    def test_rate_limits_expire_and_do_not_mix_visitors(self):
        with patch('engine.cloud_sessions.time.monotonic', return_value=10):
            self.assertTrue(self.sessions.rate('one', 'request', 1, 60))
            self.assertFalse(self.sessions.rate('one', 'request', 1, 60))
            self.assertTrue(self.sessions.rate('two', 'request', 1, 60))
        with patch('engine.cloud_sessions.time.monotonic', return_value=71):
            self.assertTrue(self.sessions.rate('one', 'request', 1, 60))
