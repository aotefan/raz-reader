"""On-demand local OCR for one indexed PDF page; originals are never written."""
import hashlib
import csv
import io
import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from engine.pipeline import contained


def text_items(tsv, page_size=None, offset=(0, 0)):
    """Group real Tesseract word boxes into lines in its reading order."""
    lines = {}
    width = height = 0
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row['level'] == '1':
            width, height = int(row['width']), int(row['height'])
        if row['level'] != '5' or not row['text'].strip() or float(row['conf']) < 30:
            continue
        key = (row['block_num'], row['par_num'], row['line_num'])
        lines.setdefault(key, []).append(row)
    if width <= 0 or height <= 0:
        raise ValueError('Missing OCR page geometry')
    if page_size is not None:
        width, height = page_size
    dx, dy = offset
    items = []
    for words in lines.values():
        text = ' '.join(w['text'] for w in words)
        if not re.search('[A-Za-z0-9]', text):
            continue
        x = dx + min(int(w['left']) for w in words)
        y = dy + min(int(w['top']) for w in words)
        bottom = dy + max(int(w['top']) + int(w['height']) for w in words)
        size = (bottom - y) / height * 1000
        items.append({'str': text, 'height': size,
                      'transform': [1, 0, 0, size, x / width * 1000, (1 - bottom / height) * 1000],
                      'words': [{'text': w['text'], 'x': (dx + int(w['left'])) / width,
                                 'y': (dy + int(w['top'])) / height, 'width': int(w['width']) / width,
                                 'height': int(w['height']) / height} for w in words]})
    return items


def has_reading_text(items):
    """A detected footer alone does not mean the picture-book caption was read."""
    footer = re.compile(r'\blevel\s+(?:aa|[a-z][12]?)\b|www\.|reading[a-]?z\.com|all rights reserved|copyright|©|^(?:written|illustrated|photographs|illustrations)\s+by\b', re.I)
    return any(re.search(r'[A-Za-z]{2,}', i['str']) and not footer.search(i['str']) for i in items)


def fragmented_caption(items):
    """Detect a horizontal caption wrongly segmented into separate tiny columns.

    Tesseract can read individual sideways glyph groups instead of one line.
    A real line such as 'I am' or 'Go!' must not be mistaken for this failure.
    """
    candidates = []
    for item in items:
        transform = item.get('transform')
        height = item.get('height', 0)
        if not transform or not 14 <= height <= 75:
            continue
        bottom = 1000 - transform[5]
        words = re.findall(r'[A-Za-z]+', item.get('str', ''))
        if .78 * 1000 <= bottom <= .92 * 1000 and words:
            if len(words) > 1:
                return False
            candidates.append((transform[4], bottom, height))
    return (len(candidates) >= 3
            and max(c[0] for c in candidates) - min(c[0] for c in candidates) > 100
            and max(c[1] for c in candidates) - min(c[1] for c in candidates)
                < max(c[2] for c in candidates) * .7)


class PageText:
    def __init__(self, data):
        self.directory = Path(data) / 'page-text'
        self.lock = threading.Lock()

    def read(self, book, page):
        if type(page) is not int or not 1 <= page <= book['page_count']:
            raise ValueError('Invalid page')
        source = contained(book['source_path'], book['source_root'])
        stat = source.stat()
        key = hashlib.sha256(f"{book['edition_id']}:{stat.st_size}:{stat.st_mtime_ns}:{page}:tesseract-eng-v5-no-annotations".encode()).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        cache = self.directory / (key + '.json')
        with self.lock:
            if cache.is_file():
                try:
                    saved = json.loads(cache.read_text())
                    if not fragmented_caption(saved.get('items', [])):
                        return saved
                except (ValueError, OSError):
                    pass  # Rebuild an interrupted/corrupt cache on demand.
            try:
                with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
                    prefix = Path(temporary) / 'page'
                    subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile',
                                    '-scale-to', '2200', '-hide-annotations', '-png', str(source), str(prefix)],
                                   check=True, capture_output=True, timeout=30)
                    result = subprocess.run(['tesseract', str(prefix) + '.png', 'stdout', '-l', 'eng', 'tsv'],
                                            check=True, capture_output=True, text=True, timeout=30)
                    items = text_items(result.stdout)
                    method = 'full_page'
                    if not has_reading_text(items) or fragmented_caption(items):
                        # Large illustrations / media buttons can make automatic
                        # segmentation skip a short caption. Retry the lower band
                        # of this page only, suppressing annotation appearances.
                        geometry = next(r for r in csv.DictReader(io.StringIO(result.stdout), delimiter='\t') if r['level'] == '1')
                        width, height = int(geometry['width']), int(geometry['height'])
                        top = int(height * .8)
                        band = Path(temporary) / 'caption'
                        subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile',
                                        '-scale-to', '2200', '-hide-annotations', '-y', str(top), '-H', str(height-top),
                                        '-png', str(source), str(band)], check=True, capture_output=True, timeout=30)
                        # Automatic layout detection can still classify the whole
                        # band as an illustration (notably Big, page 5 on Linux).
                        # Sparse-text segmentation reads the same crop/word boxes
                        # without requiring a detected text block. Only retry it
                        # if the usual segmentation found no reading text.
                        for segmentation in ('3', '11'):
                            retry = subprocess.run(['tesseract', str(band)+'.png', 'stdout', '-l', 'eng',
                                                    '--psm', segmentation, 'tsv'],
                                                   check=True, capture_output=True, text=True, timeout=30)
                            captions = text_items(retry.stdout, (width, height), (0, top))
                            if has_reading_text(captions) and not fragmented_caption(captions):
                                items = captions; method = 'caption_band_fallback'
                                break
                        if fragmented_caption(items):
                            raise ValueError('Caption OCR remains fragmented')
                output = {'items': items, 'view': [0, 0, 1000, 1000], 'source': 'Tesseract eng', 'method': method,
                          'page': page, 'offline': True, 'reviewed': False}
                temporary_cache = cache.with_suffix('.tmp')
                temporary_cache.write_text(json.dumps(output, ensure_ascii=False))
                temporary_cache.replace(cache)
                return output
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
                raise RuntimeError('本页文字识别暂不可用，请重试；请确认已安装 Poppler 和 Tesseract 英文识别。') from error
