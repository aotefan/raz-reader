import io
import tempfile
import unittest
import wave
from pathlib import Path
from engine.recordings import Recordings


def sample(value=0):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(value.to_bytes(2, 'little') * 1600)
    return output.getvalue()


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.store = Recordings(self.data)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, edition='book-a', page=1, value=0):
        version = self.store.reserve(edition, page)
        self.assertTrue(self.store.save(edition, page, version, 'audio/wav', sample(value)))
        return version

    def test_replace_keeps_one_latest_take_without_changing_other_pages_or_editions(self):
        self.save()
        self.save(page=2)
        self.save(edition='book-b')
        latest = self.save(value=11)
        self.assertEqual(self.store.list('book-a')['1']['version'], latest)
        self.assertEqual(self.store.read('book-a', 1)[1], sample(11))
        self.assertEqual(self.store.read('book-a', 2)[1], sample())
        self.assertEqual(self.store.read('book-b', 1)[1], sample())
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recordings WHERE audio IS NOT NULL').fetchone()[0], 3)
            self.assertEqual(db.execute('PRAGMA secure_delete').fetchone()[0], 1)

    def test_late_old_take_and_duplicate_retry_do_not_overwrite_newer_audio(self):
        old = self.store.reserve('book-a', 1)
        new = self.save(value=12)
        self.assertFalse(self.store.save('book-a', 1, old, 'audio/wav', sample(10)))
        self.assertFalse(self.store.save('book-a', 1, new, 'audio/wav', sample(10)))
        self.assertEqual(self.store.read('book-a', 1)[1], sample(12))

    def test_failed_or_cancelled_replacement_preserves_previous_recording(self):
        self.save(value=13)
        version = self.store.reserve('book-a', 1)
        self.assertEqual(self.store.read('book-a', 1)[1], sample(13))
        with self.assertRaises(ValueError):
            self.store.save('book-a', 1, version, 'audio/webm', b'not audio')
        self.assertEqual(self.store.read('book-a', 1)[1], sample(13))

    def test_reopening_storage_restores_audio_and_monotonic_take_versions(self):
        version = self.save(value=14)
        reopened = Recordings(self.data)
        self.assertEqual(reopened.read('book-a', 1)[1], sample(14))
        self.assertEqual(reopened.reserve('book-a', 1), version + 1)

    def test_unknown_take_wrong_mime_and_empty_audio_are_rejected(self):
        version = self.store.reserve('book-a', 1)
        for take, mime, audio in [(version + 1, 'audio/wav', sample()),
                                  (version, 'text/html', sample()), (version, 'audio/wav', b''),
                                  (version, 'audio/wav', b'0' * (20 * 1024 * 1024 + 1))]:
            with self.assertRaises(ValueError):
                self.store.save('book-a', 1, take, mime, audio)
        self.assertEqual(self.store.list('book-a'), {})
        self.assertIsNone(self.store.read('book-a', 1))

    def test_ids_are_database_keys_never_filesystem_paths(self):
        self.save(edition='../../escape')
        self.assertEqual({p.name for p in self.data.iterdir()}, {'recordings.sqlite3'})
