"""One page-bound reading reference for translation and pronunciation scoring.

OCR observations are candidates, not a transcript. Isolate the prose block and
require independent layout passes to agree before publishing a reading target.
No vocabulary guessing, score adjustment, or modification of source PDFs.
"""
import csv
import hashlib
import io
import json
import re
import statistics
import subprocess
import tempfile
import threading
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

from engine.pipeline import contained

POLICY = 'reading-reference-v2'
UNCERTAIN = '本页正文暂未识别准确，请稍后重试。'
METADATA = re.compile(r'^(?:written|illustrated|photographs|illustrations)\s+by\b|'
                      r'\blevel(?:ed book)?\s+(?:aa|[a-z][12]?)\b|www\.|'
                      r'reading\s*a[ -]?z\s*\.\s*com|copyright|all rights reserved|©', re.I)


def tokens(text):
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", unicodedata.normalize('NFKC', text).lower().replace('’', "'"))


def bounds(item):
    words = item['words']
    return (min(w['x'] for w in words), min(w['y'] for w in words),
            max(w['x'] + w['width'] for w in words), max(w['y'] + w['height'] for w in words))


def line_item(words):
    left = min(w['x'] for w in words)
    top = min(w['y'] for w in words)
    bottom = max(w['y'] + w['height'] for w in words)
    return {'str': ' '.join(w['text'] for w in words), 'height': (bottom-top)*1000,
            'transform': [1, 0, 0, (bottom-top)*1000, left*1000, (1-bottom)*1000],
            'words': words}


def ocr_items(tsv, size, offset=(0, 0)):
    """Keep confidence and *all* words: silently dropping low-confidence words
    would make two incomplete transcripts appear to agree."""
    grouped = {}
    width, height = size
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        if row['level'] != '5' or not row['text'].strip():
            continue
        key = (row['block_num'], row['par_num'], row['line_num'])
        grouped.setdefault(key, []).append({
            'text': row['text'], 'confidence': float(row['conf']),
            'x': (offset[0]+int(row['left']))/width, 'y': (offset[1]+int(row['top']))/height,
            'width': int(row['width'])/width, 'height': int(row['height'])/height})
    return [line_item(words) for words in grouped.values()]


def prose(item):
    text = item['str'].strip()
    return bool(re.search('[A-Za-z]', text)) and not METADATA.search(text)


def confident(item, minimum=55, average=80):
    words = [w for w in item['words'] if re.search('[A-Za-z0-9]', w['text'])]
    return bool(words) and min(w.get('confidence', 100) for w in words) >= minimum and statistics.mean(
        w.get('confidence', 100) for w in words) >= average


def consensus(passes):
    groups = {}
    for items in passes:
        if any(re.search(r'[|\\{}_=~]', i['str']) for i in items):
            continue  # Do not normalize an unrecognized glyph out of a word.
        words = [w for i in items for w in i['words'] if tokens(w['text'])]
        key = tuple(t for w in words for t in tokens(w['text']))
        # One OCR word can contain punctuation-separated tokens. Compare their
        # text without losing the associated confidence evidence.
        confidence = [w.get('confidence',100) for w in words for _ in tokens(w['text'])]
        if key:
            groups.setdefault(key,[]).append((items,confidence))
    for key, variants in sorted(groups.items(), key=lambda pair: -len(pair[1])):
        if len(variants)<2:
            continue
        best = [max(v[1][i] for v in variants) for i in range(len(key))]
        # Three matching raster/layout readings supply more evidence than two;
        # no low-confidence word is removed to manufacture that agreement.
        minimum,average=(60,75) if len(variants)>=3 else (65,80)
        if min(best)<minimum or statistics.mean(best)<average:
            continue
        if any(t.isdigit() and max(v[1][i] for v in variants)<95 for i,t in enumerate(key)):
            continue  # Ambiguous I/1 and O/0 must not silently change the target.
        if any(len(t)==1 and t not in ('a','i') and not t.isdigit() for t in key):
            continue
        return max(variants,key=lambda v:statistics.mean(v[1]))[0]
    return None


def reading_block(items):
    """Find a connected prose block by line spacing and typography, not a list
    of banned tokens. Small picture labels and isolated art are not sentences."""
    lines = [i for i in items if prose(i) and .10 < bounds(i)[3] <= .93
             and .014 <= i['height']/1000 <= .085]
    seeds = [i for i in lines if confident(i, 15, 45)
             and (len(tokens(i['str'])) >= 2 or re.search(r'[.!?][”\"\']?$', i['str'].strip()))]
    if not seeds:
        return []
    seed = max(seeds, key=lambda i: bounds(i)[3])
    selected = [seed]
    size = seed['height']/1000
    # Confidence may anchor a block, but must never remove a neighbouring line:
    # one weakly recognized word otherwise crops the entire sentence away before
    # the later OCR passes can verify it (and falsely confirm a partial page).
    pending = [i for i in lines if i is not seed and size*.58 <= i['height']/1000 <= size*1.7]
    while pending:
        found = []
        for item in pending:
            x0, y0, x1, y1 = bounds(item)
            for other in selected:
                a, b, c, d = bounds(other)
                if abs(y1-d) <= max(item['height'],other['height'])/1000*2.6 and (
                        min(x1,c)>max(x0,a) or abs(x0-a)<size*2):
                    found.append(item); break
        if not found:
            break
        selected.extend(found)
        pending = [i for i in pending if i not in found]
    # A second genuine paragraph may sit above the illustration. Do not silently
    # score only the convenient bottom paragraph when the layout is ambiguous.
    outside = [i for i in lines if i not in selected and confident(i,0,50)
               and len(tokens(i['str'])) >= 4 and i['height']/1000 >= size*.58]
    if outside:
        return []
    return sorted(selected, key=lambda i: (round(bounds(i)[1], 2), bounds(i)[0]))


def read_pgm(path):
    data = path.read_bytes()
    # Poppler writes P5, dimensions, and maxval; comments are permitted by PNM.
    match = re.match(rb'P5\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+255(?:\r\n|\n| )', data)
    if not match:
        raise ValueError('Invalid rendered page')
    size = (int(match[1]), int(match[2]))
    pixels = data[match.end():]
    if len(pixels) != size[0]*size[1]:
        raise ValueError('Incomplete rendered page')
    return size, pixels


def crop_pgm(path, size, pixels, rect):
    width, height = size
    x0,y0,x1,y1 = rect
    x0,y0 = max(0,int(x0*width)),max(0,int(y0*height))
    x1,y1 = min(width,int(x1*width+.999)),min(height,int(y1*height+.999))
    crop = b''.join(pixels[y*width+x0:y*width+x1] for y in range(y0,y1))
    path.write_bytes(f'P5\n{x1-x0} {y1-y0}\n255\n'.encode()+crop)
    return x0,y0


def native_items(source, page):
    result = subprocess.run(['pdftotext','-f',str(page),'-l',str(page),'-bbox-layout',str(source),'-'],
                            check=True,capture_output=True,text=True,timeout=20)
    root = ET.fromstring(result.stdout)
    sheet = root.find('.//{*}page')
    if sheet is None:
        return []
    width,height = float(sheet.attrib['width']),float(sheet.attrib['height'])
    items = []
    for line in sheet.findall('.//{*}line'):
        words=[]
        for w in line.findall('{*}word'):
            if not w.text:
                continue
            a=w.attrib
            words.append({'text':w.text,'x':float(a['xMin'])/width,'y':float(a['yMin'])/height,
                          'width':(float(a['xMax'])-float(a['xMin']))/width,
                          'height':(float(a['yMax'])-float(a['yMin']))/height})
        if words:items.append(line_item(words))
    return items


class ReadingText:
    def __init__(self, data):
        self.directory = Path(data)/'reading-text'
        self.lock = threading.Lock()
        self.digests = {}

    def identity(self, book, page):
        if type(page) is not int or not 1 <= page <= book['page_count']:
            raise ValueError('Invalid page')
        source = contained(book['source_path'], book['source_root'])
        stat = source.stat()
        key = (str(source),stat.st_size,stat.st_mtime_ns)
        if key not in self.digests:
            self.digests[key] = hashlib.sha256(source.read_bytes()).hexdigest()
        return source,self.digests[key]

    @staticmethod
    def recognize(path, size, mode, offset=(0,0)):
        r = subprocess.run(['tesseract',str(path),'stdout','-l','eng','--psm',str(mode),
                            '-c','textord_tabfind_vertical_text=0','tsv'],
                           check=True,capture_output=True,text=True,timeout=30)
        return ocr_items(r.stdout,size,offset)

    def extract(self, source, page, temporary):
        native = native_items(source,page)
        if native:
            # Digital PDFs already supply actual text, not image guesses. Keep
            # their paragraphs, including prose near the top of a page; exclude
            # metadata and smaller marginal print using their real typography.
            prose_items=[i for i in native if prose(i)]
            largest=max((i['height'] for i in prose_items),default=0)
            items=[i for i in prose_items if not (bounds(i)[3]>.86 and i['height']<largest*.7)]
            return items, 'pdf-text', bool(items)
        prefix=Path(temporary)/'page'
        subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-singlefile','-scale-to','2200',
                        '-hide-annotations','-gray',str(source),str(prefix)],
                       check=True,capture_output=True,timeout=30)
        image=prefix.with_suffix('.pgm');size,pixels=read_pgm(image)
        observations=self.recognize(image,size,3)
        block=reading_block(observations)
        if not block:
            # Sparse layout is for discovery only. Its guesses inside pictures
            # never become a reading reference without the block checks below.
            observations=self.recognize(image,size,11)
            block=reading_block(observations)
        if not block:
            # A large bordered illustration can make full-page layout analysis
            # miss an otherwise clear caption altogether. Discover text in
            # overlapping horizontal strips as well, then apply the same block
            # and independent-pass checks. Inspect the whole page so a second
            # paragraph above the picture is not silently dropped.
            for index,(top,bottom) in enumerate(((.04,.50),(.40,.77),(.65,.93))):
                strip=Path(temporary)/f'discovery-{index}.pgm'
                offset=crop_pgm(strip,size,pixels,(.025,top,.975,bottom))
                for item in self.recognize(strip,size,11,offset):
                    a,b,c,d=bounds(item)
                    duplicate=any(tokens(item['str'])==tokens(other['str'])
                        and abs(b-bounds(other)[1]) < .01
                        and abs(a-bounds(other)[0]) < .02 for other in observations)
                    if not duplicate:
                        observations.append(item)
            block=reading_block(observations)
            if not block:
                return [],'ocr-block-consensus',False
        boxes=[bounds(i) for i in block]
        font=statistics.median(i['height']/1000 for i in block)
        # Keep the entire line width. Cropping to the detected words would cut
        # off a word missed by discovery and falsely "confirm" its omission.
        rect=(.025,max(0,min(b[1] for b in boxes)-font*.55),
              .975,min(.93,max(b[3] for b in boxes)+font*.45))
        region=Path(temporary)/'body.pgm'
        offset=crop_pgm(region,size,pixels,rect)
        primary=self.recognize(region,size,6,offset)
        passes=[[i for i in primary if prose(i)]]
        # Change raster scale as well as layout mode. Repeating the same OCR on
        # the same pixels is not useful corroboration of a systematic error.
        for scale,mode in [(1500,3 if len(block)>1 else 7),(1800,6),(1000,6)]:
            factor=scale/max(size);scaled=(round(size[0]*factor),round(size[1]*factor))
            x,y=int(rect[0]*scaled[0]),int(rect[1]*scaled[1])
            width=int(rect[2]*scaled[0])-x;height=int(rect[3]*scaled[1])-y
            retry=Path(temporary)/('check-'+str(scale))
            subprocess.run(['pdftoppm','-f',str(page),'-l',str(page),'-singlefile',
                            '-scale-to',str(scale),'-hide-annotations','-gray',
                            '-x',str(x),'-y',str(y),'-W',str(width),'-H',str(height),
                            str(source),str(retry)],check=True,capture_output=True,timeout=30)
            candidate=self.recognize(retry.with_suffix('.pgm'),scaled,mode,(x,y))
            passes.append([i for i in candidate if prose(i)])
            accepted=consensus(passes)
            if accepted is not None:
                return accepted,'ocr-block-consensus',True
        # Low-contrast scans can retain the same words at every scale but weak
        # confidence. A bounded contrast pass is additional evidence, never a
        # reason to lower the acceptance threshold or invent a missing word.
        crop_size,crop_pixels=read_pgm(region)
        binary=Path(temporary)/'body-contrast.pgm'
        binary.write_bytes(f'P5\n{crop_size[0]} {crop_size[1]}\n255\n'.encode()+
                           crop_pixels.translate(bytes(0 if i<180 else 255 for i in range(256))))
        candidate=self.recognize(binary,size,6,offset)
        passes.append([i for i in candidate if prose(i)])
        accepted=consensus(passes)
        if accepted is not None:
            return accepted,'ocr-block-consensus',True
        return [],'ocr-block-consensus',False

    def read(self, book, page):
        try:
            source,digest=self.identity(book,page)
        except (OSError,KeyError) as error:
            raise RuntimeError('本页资料暂不可读，无法确认正文。') from error
        self.directory.mkdir(parents=True,exist_ok=True)
        cache=self.directory/(digest+'-'+str(page)+'.json')
        with self.lock:
            if cache.exists():
                try:
                    saved=json.loads(cache.read_text())
                    reading=saved.get('reading',{})
                    expected=hashlib.sha256(f"{POLICY}:{digest}:{page}:{reading.get('text','')}".encode()).hexdigest()
                    if (reading.get('policy')==POLICY and reading.get('status')=='ready'
                            and reading.get('source_sha256')==digest and reading.get('id')==expected
                            and saved.get('page')==page and reading.get('text')
                            and ' '.join(i['str'] for i in saved.get('items',[])).strip()==reading['text']):
                        return saved
                except (ValueError,OSError):
                    pass
            try:
                with tempfile.TemporaryDirectory(dir=self.directory) as temp:
                    items,method,ready=self.extract(source,page,temp)
                text=' '.join(i['str'] for i in items).strip() if ready else ''
                reference_id=hashlib.sha256(f'{POLICY}:{digest}:{page}:{text}'.encode()).hexdigest() if ready else None
                result={'items':items if ready else [],'view':[0,0,1000,1000],'source':'Tesseract eng',
                        'method':method,'page':page,'offline':True,'reviewed':False,
                        'reading':{'status':'ready' if ready else 'uncertain','text':text,'policy':POLICY,
                                   'id':reference_id,'source_sha256':digest,
                                   'error':None if ready else UNCERTAIN}}
                if ready:
                    pending=cache.with_suffix('.tmp');pending.write_text(json.dumps(result,ensure_ascii=False));pending.replace(cache)
                return result
            except (OSError,subprocess.SubprocessError,ValueError,KeyError,ET.ParseError) as error:
                raise RuntimeError('本页正文识别暂不可用，录音会保留，请稍后重试。') from error

    def reference(self, book, page):
        reading=self.read(book,page)['reading']
        if reading['status']!='ready':
            raise ValueError(UNCERTAIN+'录音已保留，暂不评分。')
        return reading
