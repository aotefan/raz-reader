import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from engine.assessment import Assessment, METHOD, reference_text
from engine.recordings import Recordings
from tests.test_recordings import sample


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.store = Recordings(self.data)

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, page=1):
        version = self.store.reserve('book', page)
        self.store.save('book', page, version, 'audio/wav', sample())
        return version

    def test_latest_take_replaces_its_score_only_and_rejects_late_score(self):
        old = self.save(); other = self.save(2)
        self.store.save_assessment('book', 1, old, {'score': 88, 'version': old})
        self.store.save_assessment('book', 2, other, {'score': 77, 'version': other})
        latest = self.save()
        self.assertFalse(self.store.save_assessment('book', 1, old, {'score': 88}))
        self.assertIsNone(self.store.list('book')['1']['assessment'])
        self.assertEqual(self.store.list('book')['2']['assessment']['score'], 77)
        self.assertTrue(self.store.save_assessment('book', 1, latest, {'score': 93}))
        self.assertEqual(Recordings(self.data).list('book')['1']['assessment']['score'], 93)

    def test_migration_preserves_existing_microphone_bytes(self):
        path = self.data / 'old'; path.mkdir()
        with sqlite3.connect(path / 'recordings.sqlite3') as db:
            db.execute('CREATE TABLE recordings (edition TEXT, page INTEGER, next_version INTEGER, version INTEGER, mime TEXT, audio BLOB, updated_at TEXT, PRIMARY KEY(edition,page))')
            db.execute('INSERT INTO recordings VALUES (?,?,?,?,?,?,?)', ('book', 1, 4, 4, 'audio/wav', sample(), 'yesterday'))
        store = Recordings(path)
        self.assertEqual(store.read('book', 1), ('audio/wav', sample()))
        self.assertIsNone(store.list('book')['1']['assessment'])
        self.assertEqual(store.reserve('book', 1), 5)

    def test_cached_result_needs_exact_reference_method_and_version(self):
        version = self.save()
        result = {'score': 83, 'method': METHOD, 'reference': 'A red egg.', 'version': version}
        self.store.save_assessment('book', 1, version, result)
        service = Assessment(self.data, self.store)
        with patch.object(service.worker, 'call', side_effect=AssertionError('Cache should not run model')):
            self.assertEqual(service.run('book', 1, version, 'A red egg.'), result)
        self.save()
        with self.assertRaisesRegex(ValueError, '录音已更新'):
            service.run('book', 1, version, 'A red egg.')

    def test_invalid_reference_and_version_do_not_run_model(self):
        for value in [None, [], '', '你好', 'a '*121, '<script>bad</script>']:
            with self.assertRaises(ValueError): reference_text(value)
        version = self.save()
        with patch('engine.assessment_process.AssessmentProcess.call', side_effect=AssertionError('Invalid request')):
            for value in [True, -1, 0, '1']:
                with self.assertRaises(ValueError):
                    Assessment(self.data, self.store).run('book', 1, value, 'A red egg.')
        self.assertEqual(reference_text(' A  red\negg. '), 'A red egg.')

    def test_shelf_summary_uses_latest_valid_scored_pages_and_edition_boundaries(self):
        first = self.save(1); second = self.save(2)
        self.store.save_assessment('book', 1, first, {'score': 81, 'version': first})
        self.store.save_assessment('book', 2, second, {'score': 100, 'version': second})
        self.save(3)  # Unscored pages do not count as zero.
        self.assertEqual(self.store.summaries({'book': 3}),
                         {'book': {'average': 90.5, 'scored_pages': 2}})
        latest = self.save(1)
        self.assertEqual(self.store.summaries({'book': 3})['book']['average'], 100)
        self.store.save_assessment('book', 1, latest, {'score': 60, 'version': latest})
        self.assertEqual(self.store.summaries({'book': 3})['book'], {'average': 80, 'scored_pages': 2})
        self.assertEqual(self.store.summaries({'book': 1})['book']['scored_pages'], 1)
        self.assertEqual(self.store.summaries({'other-edition': 3}), {})
        for bad in [None, [], {'score': True, 'version': latest}, {'score': 101, 'version': latest},
                    {'score': float('nan'), 'version': latest}, {'score': 99, 'version': first}]:
            self.store.save_assessment('book', 1, latest, bad)
            self.assertEqual(self.store.summaries({'book': 3})['book']['average'], 100)
        self.store.save_assessment('book', 2, second, {'score': 0, 'version': second})
        self.assertEqual(self.store.summaries({'book': 3})['book']['average'], 0)
