import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from engine.recordings import Recordings
from engine.recording_sync import pack, unpack, stamp
from tests.test_recordings import sample
from tests.test_assessment_remote import score


class RecordingReplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.context = dict(cookies='test-only-cookie', scope='u-' + 'a' * 64, editions={'book'})
        self.local = Recordings(self.path / 'mac', self.context)
        self.cloud = Recordings(self.path / 'cloud')

    def take(self, store, result=None):
        version = store.reserve('book', 1)
        store.save('book', 1, version, 'audio/wav', sample())
        if result:
            store.save_assessment('book', 1, version, {**result, 'version': version})
        return version

    def test_metadata_correction_reuses_audio_and_remaps_local_version(self):
        version = self.take(self.local, score())
        metadata, audio = unpack(pack(*self.local.sync_export('book', 1)))
        self.cloud.reserve('book', 1)  # Device-local versions need not match.
        with self.cloud.connection() as db:
            db.execute("UPDATE recordings SET reserved_at='2026-10-01T00:00:00.000000Z'")
        self.cloud.sync_import(metadata, audio)
        self.assertEqual(self.cloud.list('book')['1']['assessment']['version'], version + 1)
        self.local.save_assessment('book', 1, version, {**score(), 'version': version, 'score': 93})
        corrected, _ = self.local.sync_export('book', 1)
        self.cloud.sync_import(corrected, b'')
        self.cloud.sync_import(metadata, audio)  # Delayed older score cannot undo correction.
        result = self.cloud.list('book')['1']
        self.assertEqual(result['assessment']['score'], 93)
        self.assertEqual(result['version'], version + 1)
        self.assertEqual(self.cloud.read('book', 1)[1], sample())

    def test_download_does_not_invalidate_a_recording_already_in_flight(self):
        reserved = self.local.reserve('book', 1)
        self.take(self.cloud, score())
        self.assertTrue(self.local.sync_import(*self.cloud.sync_export('book', 1), downloading=True)['busy'])
        self.assertTrue(self.local.save('book', 1, reserved, 'audio/wav', sample()))
        self.assertEqual(self.local.list('book')['1']['version'], reserved)

    def test_mac_upload_waits_for_phone_microphone_and_abandoned_take_expires(self):
        self.take(self.local, score())
        reserved = self.cloud.reserve('book', 1)
        self.assertTrue(self.cloud.sync_import(*self.local.sync_export('book', 1))['busy'])
        # A phone take saved later wins over the earlier Mac recording.
        self.assertTrue(self.cloud.save('book', 1, reserved, 'audio/wav', sample()))
        self.assertTrue(self.cloud.sync_import(*self.local.sync_export('book', 1))['stale'])
        # Cancelled microphone requests must not permanently block sync.
        self.take(self.local, score())
        self.cloud.reserve('book', 1)
        with self.cloud.connection() as db:
            db.execute("UPDATE recordings SET reserved_at='2026-10-01T00:00:00.000000Z'")
        self.assertFalse(self.cloud.sync_import(*self.local.sync_export('book', 1))['stale'])
        self.assertEqual(self.cloud.list('book')['1']['take_id'], self.local.list('book')['1']['take_id'])

    def test_invalid_audio_result_or_future_time_never_changes_stored_take(self):
        self.take(self.local, score())
        original = self.local.list('book')
        metadata, audio = self.local.sync_export('book', 1)
        for damaged, blob in [
            ({**metadata, 'take_id': 'bad'}, audio),
            ({**metadata, 'recorded_at': '2999-01-01T00:00:00Z'}, audio),
            ({**metadata, 'assessment': {**metadata['assessment'], 'score': 999}}, audio),
            (metadata, b'not an audio file'),
        ]:
            with self.assertRaises(ValueError):
                self.local.sync_import(damaged, blob)
        self.assertEqual(self.local.list('book'), original)

    def test_restart_keeps_one_latest_queue_entry_and_no_extra_audio_copy(self):
        self.take(self.local)
        self.take(self.local, score())
        reopened = Recordings(self.path / 'mac', self.context)
        with reopened.connection() as db:
            rows = db.execute('SELECT edition,page,stamp,cookies,scope FROM recording_outbox').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][2], stamp(reopened.sync_export('book', 1)[0]))
            self.assertEqual(rows[0][3:], (self.context['cookies'], self.context['scope']))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM recordings WHERE audio IS NOT NULL').fetchone()[0], 1)
        self.cloud.sync_import(*reopened.sync_export('book', 1))
        self.assertEqual(self.cloud.list('book')['1']['assessment']['score'], 88)

    def test_existing_database_migration_preserves_audio_score_and_identity(self):
        path = self.path / 'old'
        path.mkdir()
        with sqlite3.connect(path / 'recordings.sqlite3') as db:
            db.execute('CREATE TABLE recordings (edition TEXT,page INTEGER,next_version INTEGER,version INTEGER,'
                       'mime TEXT,audio BLOB,updated_at TEXT,assessment TEXT,PRIMARY KEY(edition,page))')
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?)',
                       ('book', 1, 4, 4, 'audio/wav', sample(), '2026-10-19T01:00:00.000Z', json.dumps({**score(), 'version': 4})))
        migrated = Recordings(path, self.context)
        take = migrated.list('book')['1']
        self.assertEqual(take['version'], 4)
        self.assertEqual(take['assessment']['score'], 88)
        self.assertEqual(migrated.read('book', 1)[1], sample())
        self.assertEqual(Recordings(path).list('book')['1']['take_id'], take['take_id'])
        migrated.backfill_sync()
        self.cloud.sync_import(*migrated.sync_export('book', 1))
        self.assertEqual(self.cloud.list('book')['1']['assessment']['score'], 88)
