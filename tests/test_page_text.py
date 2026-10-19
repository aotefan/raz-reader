import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from engine.page_text import PageText, text_items, has_reading_text


TSV = ('level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
       '1\t1\t0\t0\t0\t0\t0\t0\t1000\t2000\t-1\t\n'
       '5\t1\t1\t1\t1\t1\t100\t1600\t100\t60\t95\tThe\n'
       '5\t1\t1\t1\t1\t2\t210\t1600\t200\t60\t95\tpeople.\n'
       '5\t1\t2\t1\t1\t1\t100\t400\t10\t10\t5\tnoise\n')


class PageTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / 'source.pdf'
        self.pdf.write_bytes(b'fixture')
        self.book = {'page_count': 11, 'edition_id': 'pdf_test',
                     'source_path': str(self.pdf), 'source_root': str(self.root)}
        self.reader = PageText(self.root / 'cache')

    def tearDown(self):
        self.temp.cleanup()

    def test_line_geometry_and_low_confidence_filter(self):
        items = text_items(TSV)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['str'], 'The people.')
        self.assertEqual(items[0]['height'], 30)
        self.assertAlmostEqual(items[0]['transform'][5], 170)
        self.assertEqual(items[0]['words'][1], {'text': 'people.', 'x': .21,
                                               'y': .8, 'width': .2, 'height': .03})

    def test_caption_crop_boxes_are_mapped_to_original_page(self):
        cropped = TSV.replace('1000\t2000', '1000\t400').replace('1600', '100')
        item = text_items(cropped, (1000, 2000), (0, 1600))[0]
        self.assertEqual(item['str'], 'The people.')
        self.assertAlmostEqual(item['words'][0]['y'], .85)
        self.assertAlmostEqual(item['words'][0]['height'], .03)
        self.assertAlmostEqual(item['transform'][5], 120)

    def test_empty_or_footer_only_page_retries_caption_and_caches_result(self):
        header = TSV.splitlines(keepends=True)[0]
        geometry = TSV.splitlines(keepends=True)[1]
        cropped = TSV.replace('1000\t2000', '1000\t400').replace('1600', '100')
        footer = header + geometry + '5\t1\t1\t1\t1\t1\t100\t1900\t300\t20\t95\tBook • Level aa\n'
        for n, primary in enumerate([header + geometry, footer], 1):
            with self.subTest(page=n), patch('engine.page_text.subprocess.run', side_effect=[
                subprocess.CompletedProcess([],0,''), subprocess.CompletedProcess([],0,primary),
                subprocess.CompletedProcess([],0,''), subprocess.CompletedProcess([],0,cropped),
            ]) as run:
                result = self.reader.read(self.book,n)
                self.assertEqual(result['method'],'caption_band_fallback')
                self.assertIn('-hide-annotations',run.call_args_list[2].args[0])
                self.assertEqual(self.reader.read(self.book,n),result)
                self.assertEqual(run.call_count,4)
                self.assertEqual(self.pdf.read_bytes(),b'fixture')
        self.assertFalse(has_reading_text([{'str':'10'}, {'str':'Colorful Eggs • Level aa'}]))
        self.assertTrue(has_reading_text([{'str':'A colorful egg.'}]))

    def test_cache_page_isolation_invalidation_and_source_unchanged(self):
        result = subprocess.CompletedProcess([], 0, TSV, '')
        with patch('engine.page_text.subprocess.run', return_value=result) as run:
            first = self.reader.read(self.book, 9)
            self.assertEqual(self.reader.read(self.book, 9), first)
            self.assertEqual(run.call_count, 2)
            self.reader.read(self.book, 10)
            self.assertEqual(run.call_count, 4)
            self.assertEqual(self.pdf.read_bytes(), b'fixture')
            self.pdf.write_bytes(b'changed fixture')
            self.reader.read(self.book, 9)
            self.assertEqual(run.call_count, 6)
            self.assertEqual(list(self.reader.directory.glob('*/page.png')), [])

    def test_sparse_caption_recovers_an_old_empty_cache_with_original_geometry(self):
        empty = ''.join(TSV.splitlines(keepends=True)[:2])
        cropped = TSV.replace('1000\t2000', '1000\t400').replace('1600', '100')
        stat = self.pdf.stat()
        old_key = hashlib.sha256(f"pdf_test:{stat.st_size}:{stat.st_mtime_ns}:5:tesseract-eng-v3-caption".encode()).hexdigest()
        self.reader.directory.mkdir(parents=True)
        (self.reader.directory / (old_key + '.json')).write_text(json.dumps({'items': [], 'method': 'full_page'}))
        with patch('engine.page_text.subprocess.run', side_effect=[
            subprocess.CompletedProcess([], 0, ''), subprocess.CompletedProcess([], 0, empty),
            subprocess.CompletedProcess([], 0, ''), subprocess.CompletedProcess([], 0, empty),
            subprocess.CompletedProcess([], 0, cropped),
        ]) as run:
            result = self.reader.read(self.book, 5)
            self.assertEqual(result['items'][0]['str'], 'The people.')
            self.assertAlmostEqual(result['items'][0]['words'][0]['y'], .85)
            self.assertIn('11', run.call_args.args[0])
            self.assertEqual(self.reader.read(self.book, 5), result)
            self.assertEqual(run.call_count, 5)
            self.assertEqual(self.pdf.read_bytes(), b'fixture')

    def test_blank_page_stays_empty_and_retries_are_bounded(self):
        empty = ''.join(TSV.splitlines(keepends=True)[:2])
        with patch('engine.page_text.subprocess.run', return_value=subprocess.CompletedProcess([], 0, empty)) as run:
            result = self.reader.read(self.book, 5)
            self.assertEqual(result['items'], [])
            self.assertEqual(run.call_count, 5)
            self.assertEqual(self.reader.read(self.book, 5), result)
            self.assertEqual(run.call_count, 5)

    def test_invalid_pages_and_outside_paths_never_execute(self):
        with patch('engine.page_text.subprocess.run') as run:
            for page in [0, 12, True, '../1']:
                with self.assertRaises(ValueError):
                    self.reader.read(self.book, page)
            self.book['source_root'] = str(self.root / 'other')
            with self.assertRaises(ValueError):
                self.reader.read(self.book, 9)
            run.assert_not_called()

    def test_timeout_is_retryable_and_never_cached(self):
        with patch('engine.page_text.subprocess.run', side_effect=subprocess.TimeoutExpired('ocr', 30)):
            with self.assertRaises(RuntimeError):
                self.reader.read(self.book, 9)
        self.assertEqual(list(self.reader.directory.iterdir()), [])
        with patch('engine.page_text.subprocess.run', return_value=subprocess.CompletedProcess([], 0, TSV, '')):
            self.assertEqual(self.reader.read(self.book, 9)['items'][0]['str'], 'The people.')
