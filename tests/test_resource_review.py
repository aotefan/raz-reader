import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.audit_local_resources import audit


class ResourceReviewTests(unittest.TestCase):
    def test_exact_content_duplicates_isolated_failure_cache_and_no_source_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'original'; root.mkdir()
            (root / 'A.PDF').mkdir(); (root / 'extra').mkdir()
            first = root / 'A.PDF/book.pdf'; first.write_bytes(b'good')
            duplicate = root / 'extra/book.pdf'; duplicate.write_bytes(b'good')
            broken = root / 'extra/bad.pdf'; broken.write_bytes(b'bad')
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in [first, duplicate, broken]}
            def pdf(path):
                if path.read_bytes() == b'bad': raise ValueError('Broken PDF')
                return {'page_count': 2, 'audio_detection': {'embedded_stream_count': 0, 'screen_annotations': []}}
            with patch('scripts.audit_local_resources.inspect_pdf', side_effect=pdf) as inspect:
                result = audit(root, Path(tmp) / 'output')
                self.assertEqual(result['duplicate_extra_files'], 1)
                self.assertEqual(len(result['failures']), 1)
                self.assertEqual(inspect.call_count, 3)
                audit(root, Path(tmp) / 'output')
                self.assertEqual(inspect.call_count, 3)
            for p, expected in before.items():
                self.assertEqual((p.read_bytes(), p.stat().st_mtime_ns), expected)

    def test_output_cannot_be_written_into_originals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): audit(Path(tmp), Path(tmp) / 'cache')

if __name__ == '__main__': unittest.main()
