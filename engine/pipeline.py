"""Read-only resource discovery and metadata inspection. Never extracts streams."""
import argparse
import hashlib
import json
import math
import re
import sqlite3
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = APP / 'library'
LEVELS = ['AA'] + list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + ['Z1', 'Z2']
SCHEMA_VERSION = 3


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def contained(path, root):
    p, r = Path(path).resolve(), Path(root).resolve()
    if not p.is_relative_to(r) or not p.is_file():
        raise ValueError('File is outside its authorized root or missing')
    return p


def discover(root):
    """Names and counts only; no recursive import, hashes or conversion."""
    root = Path(root).resolve()
    found = []
    for level in LEVELS:
        candidates = [root / (level + '.PDF')]
        if level == 'H':
            candidates.append(root / 'H.PF')
        folder = next((p for p in candidates if p.is_dir() and p.resolve().is_relative_to(root)), None)
        paths = [] if folder is None else sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.pdf'
            and p.resolve().is_relative_to(root))
        found.append({'level': level, 'exists': folder is not None,
                      'directory': folder.name if folder else None, 'discovered_count': len(paths)})
    return found


def connect(data):
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(data / 'library.sqlite3', timeout=15)
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS assets (path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
        record TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS progress (edition_id TEXT PRIMARY KEY, page INTEGER NOT NULL,
        completed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
      CREATE TABLE IF NOT EXISTS progress_writers (edition_id TEXT NOT NULL, writer_id TEXT NOT NULL,
        last_sequence INTEGER NOT NULL, PRIMARY KEY (edition_id, writer_id));
      CREATE TABLE IF NOT EXISTS completions (edition_id TEXT NOT NULL, day TEXT NOT NULL,
        PRIMARY KEY (edition_id, day));
    ''')
    return db


def run(args, warning_codes=()):
    p = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if p.returncode not in (0, *warning_codes):
        raise ValueError(p.stderr.strip()[:400] or 'Tool failed')
    return p.stdout


def inspect_pdf(path):
    info = run(['pdfinfo', str(path)])
    metadata = dict(re.findall(r'^([^:\n]+):[ \t]*(.*)$', info, re.M))
    structure = json.loads(run(['qpdf', '--json', '--json-stream-data=none', str(path)], warning_codes=(3,)))
    objects = structure['qpdf'][1]

    def resolve(value):
        if isinstance(value, str) and re.fullmatch(r'\d+ \d+ R', value):
            obj = objects.get('obj:' + value, {})
            return obj.get('value', obj.get('stream', {}).get('dict', {}))
        return value

    def node(value):
        value = resolve(value)
        return value if isinstance(value, dict) else {}

    page_objects = {p['object']: p['pageposfrom1'] for p in structure['pages']}
    screens = []
    # Metadata only: follow Screen -> A -> R -> C -> D -> EF, never read stream data.
    for key, obj in objects.items():
        val = obj.get('value', {})
        if not isinstance(val, dict) or val.get('/Subtype') != '/Screen':
            continue
        action = node(val.get('/A'))
        rendition = node(action.get('/R'))
        clip = node(rendition.get('/C'))
        spec = node(clip.get('/D'))
        ef = node(spec.get('/EF'))
        pages = [page_objects[val['/P']]] if val.get('/P') in page_objects else []
        if not pages:
            for p in structure['pages']:
                annots = resolve(node(p['object']).get('/Annots', []))
                if isinstance(annots, list) and key[4:] in annots:
                    pages.append(p['pageposfrom1'])
        screens.append({'annotation': key[4:], 'pages': pages,
                        'rendition': action.get('/R'), 'media_type': clip.get('/CT'),
                        'embedded_stream_refs': sorted(set(ef.values()))})
    streams = sorted({ref for s in screens for ref in s['embedded_stream_refs'] if isinstance(ref, str)})
    rich = []
    def named_assets(value, visited=None):
        visited = set() if visited is None else visited
        if isinstance(value, str):
            if value in visited: return []
            visited.add(value)
        tree = node(value)
        names = resolve(tree.get('/Names', []))
        result = names[1::2] if isinstance(names, list) else []
        kids = resolve(tree.get('/Kids', []))
        for child in kids if isinstance(kids, list) else []:
            result += named_assets(child, visited)
        return result
    for page in structure['pages']:
        annots = resolve(node(page['object']).get('/Annots', []))
        for ref in annots if isinstance(annots, list) else []:
            annotation = node(ref)
            if annotation.get('/Subtype') != '/RichMedia': continue
            content = node(annotation.get('/RichMediaContent'))
            for spec_ref in named_assets(content.get('/Assets')):
                spec = node(spec_ref)
                for stream_ref in set(node(spec.get('/EF')).values()):
                    if not isinstance(stream_ref, str): continue
                    stream = node(stream_ref)
                    # Never execute/expose the bundled Flash player. Audio streams only.
                    if stream.get('/Subtype') not in ('/audio/mpeg', '/audio#2Fmpeg'): continue
                    rich.append({'page': page['pageposfrom1'], 'annotation': ref,
                                 'stream_ref': stream_ref, 'filename': spec.get('/UF', spec.get('/F', '')),
                                 'kind': 'RichMedia', 'rect': annotation.get('/Rect')})
    capabilities = structure.get('encrypt', {}).get('capabilities', {})
    return {'page_count': int(metadata['Pages']), 'metadata_title': metadata.get('Title', '').strip(),
            'permissions': {'encrypted': structure.get('encrypt', {}).get('encrypted', False),
                            'copy_allowed': capabilities.get('extract', False),
                            'reading': 'opens_without_password', 'raw': metadata.get('Encrypted', 'no')},
            'audio_detection': {'status': 'detected_unverified_restricted' if streams or rich else 'no_embedded_audio_detected',
                                'screen_annotations': screens, 'embedded_stream_count': len(streams),
                                'payload_extracted': False, 'content_verified': False, 'rich_media_audio': rich}}


def index_pdf(db, path, root, level, authorized=False):
    path = contained(path, root)
    stat = path.stat()
    fingerprint = f'{SCHEMA_VERSION}:{stat.st_size}:{stat.st_mtime_ns}:{level}:{authorized}'
    old = db.execute('SELECT fingerprint, record FROM assets WHERE path=?', (str(path),)).fetchone()
    if old and old[0] == fingerprint:
        return json.loads(old[1]), True
    sha = digest(path)
    # Path+level keeps same-name books distinct; content hash identifies exact assets/editions.
    identity = f'{level}:{path.relative_to(Path(root).resolve()).as_posix()}'
    record = {'book_id': 'book_' + hashlib.sha256(identity.encode()).hexdigest()[:24],
              'edition_id': 'pdf_' + sha, 'asset_id': 'sha256:' + sha, 'sha256': sha,
              'level': level, 'source_path': str(path), 'source_root': str(Path(root).resolve()),
              'source_reference': path.relative_to(Path(root).resolve()).as_posix(),
              'source': 'self_authored_fixture' if authorized else 'user_local_RAZ',
              'authorization': {'status': 'local_test_authorized' if authorized else 'unknown_distribution_rights',
                                'derive': authorized, 'redistribute': False},
              'title': path.stem, 'title_status': 'filename_candidate', 'demo': authorized,
              'reader_source': {'kind': 'pdf', 'image_pages': None},
              'cover': {'kind': 'pdf_page', 'page': 1, 'persistent_image': False},
              'audio': {'status': 'unavailable', 'page_map': {}, 'whole_book': None},
              'validation': 'pending'}
    try:
        record.update(inspect_pdf(path))
        if record['metadata_title']:
            record['title'] = record['metadata_title']
            record['title_status'] = 'metadata_candidate'
        record['pages'] = [{'number': i, 'pdf_page': i, 'image': None, 'audio': None,
                            'ocr_boxes': None, 'validation': 'structure_only'}
                           for i in range(1, record['page_count'] + 1)]
        record['validation'] = 'structure_checked_render_pending'
    except (ValueError, subprocess.TimeoutExpired, KeyError, TypeError) as e:
        record.update({'validation': 'failed', 'error': str(e), 'page_count': 0, 'pages': []})
    db.execute('INSERT OR REPLACE INTO assets VALUES (?, ?, ?)', (str(path), fingerprint, json.dumps(record)))
    db.commit()
    return record, False


def validate_audio_map(book, manifest, root):
    """Content attestation + exact edition/hashes; filenames never suffice."""
    if not isinstance(manifest, dict):
        raise ValueError('Audio manifest must be an object')
    if manifest.get('edition_id') != book['edition_id']:
        raise ValueError('Audio mapping requires the exact PDF edition')
    external = manifest.get('verification') == 'local_transcript_and_book_checked'
    if not external and manifest.get('verification') != 'self_authored_transcript_and_page_checked':
        raise ValueError('Missing explicit content/page verification')
    provenance = manifest.get('provenance')
    entries = manifest.get('pages', [])
    if not isinstance(provenance, str) or not provenance.strip() or not isinstance(entries, list):
        raise ValueError('Missing provenance or invalid page list')
    if external:
        evidence = manifest.get('content_evidence')
        if not isinstance(evidence, dict):
            raise ValueError('External narration requires an evidence object')
        whole = manifest.get('whole_book')
        automated = evidence.get('result') == 'automated_asr_content_match'
        automated_ok = (automated and evidence.get('method') == 'local_asr_pdf_comparison'
                        and isinstance(evidence.get('asr_pdf_token_agreement'), (int, float))
                        and evidence['asr_pdf_token_agreement'] >= 0.90
                        and isinstance(evidence.get('pdf_token_coverage'), (int, float))
                        and evidence['pdf_token_coverage'] >= 0.35
                        and isinstance(evidence.get('transcript_word_count'), int)
                        and evidence['transcript_word_count'] >= 20)
        if (entries or not isinstance(whole, dict) or 'timeline' in whole
                or manifest.get('usage') != 'user_provided_local_only'
                or evidence.get('pdf_sha256') != book['sha256']
                or evidence.get('audio_sha256') != whole.get('sha256')
                or (evidence.get('result') != 'reviewed_content_match' and not automated_ok)
                or evidence.get('method') != 'local_asr_pdf_comparison'
                or not isinstance(evidence.get('notes'), str) or not evidence['notes'].strip()):
            raise ValueError('External narration requires exact content evidence; page timing is not verified')

    def validate_asset(entry):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry[k].strip()
                                                 for k in ('file', 'sha256', 'transcript')):
            raise ValueError('Audio asset requires a file, digest and checked transcript')
        path = contained(Path(root) / entry['file'], root)
        if digest(path) != entry['sha256']:
            raise ValueError('Audio digest mismatch')
        probe = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))
        if not any(s.get('codec_type') == 'audio' and s.get('codec_name') == 'mp3' for s in probe['streams']):
            raise ValueError('Expected a decoded MP3 audio stream')
        duration = float(probe['format']['duration'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('Empty audio')
        run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-f', 'null', '-'])
        return {**entry, 'duration': duration, 'asset_id': 'sha256:' + entry['sha256']}

    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('Audio page entry must be an object')
        page = entry.get('page')
        if type(page) is not int or not 1 <= page <= book['page_count'] or str(page) in result:
            raise ValueError('Invalid or duplicate audio page')
        result[str(page)] = {**validate_asset(entry), 'verification': manifest['verification']}
    whole_book = manifest.get('whole_book')
    if whole_book is not None:
        if (not isinstance(whole_book, dict) or 'page' in whole_book
                or whole_book.get('verification') != ('local_transcript_and_book_checked' if external
                                                      else 'self_authored_transcript_and_book_checked')):
            raise ValueError('Whole-book narration requires separate content verification, without page mapping')
        whole_book = validate_asset(whole_book)
        if 'timeline' in whole_book:
            timeline = whole_book['timeline']
            if (whole_book.get('timeline_verification') != 'self_authored_pcm_concatenation_checked'
                    or not isinstance(whole_book.get('timeline_provenance'), str)
                    or not whole_book['timeline_provenance'].strip()
                    or not isinstance(timeline, list) or len(timeline) != book['page_count']):
                raise ValueError('Whole-book timeline requires complete verified page boundaries')
            previous_end = 0
            for number, cue in enumerate(timeline, 1):
                if not isinstance(cue, dict) or type(cue.get('page')) is not int or cue['page'] != number:
                    raise ValueError('Timeline pages must be ordered, complete and unique')
                start, end = cue.get('start'), cue.get('end')
                if (any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end))
                        or start < 0 or end <= start or abs(start - previous_end) > 0.0001
                        or end > whole_book['duration'] + 0.05):
                    raise ValueError('Invalid, overlapping or out-of-range timeline boundary')
                previous_end = end
            if abs(previous_end - whole_book['duration']) > 0.1:
                raise ValueError('Timeline must cover the complete narration')
    status = 'verified_page_audio' if result else 'verified_whole_book_audio' if whole_book else 'unavailable'
    return {'status': status, 'page_map': result, 'whole_book': whole_book, 'provenance': provenance,
            **({'usage': manifest['usage'], 'content_evidence': manifest['content_evidence']} if external else {})}


SAMPLES = []  # Import only explicitly selected user-owned materials.


def build(root=DEFAULT_ROOT, data=APP / 'data', fixture=APP / 'fixtures/generated'):
    root, data, fixture = Path(root).resolve(), Path(data).resolve(), Path(fixture).resolve()
    db = connect(data)
    books, errors, skipped = [], [], 0
    selection_path = data / 'library-selection.json'
    selection = json.loads(selection_path.read_text()) if selection_path.is_file() else None
    entries = selection['books'] if selection else [{'level': level, 'path': path} for level, path in SAMPLES]
    candidate_index = {}
    candidate_report = data / 'i-z-library-report.json'
    if candidate_report.is_file():
        try:
            candidate_index = {row['edition_id']: row for row in
                               json.loads(candidate_report.read_text()).get('audio_candidates', [])}
        except (ValueError, KeyError, TypeError):
            candidate_index = {}
    for entry in entries:
        level, relative = entry['level'], entry['path']
        if level not in LEVELS or Path(relative).is_absolute():
            raise ValueError('Invalid library selection')
        try:
            rec, cached = index_pdf(db, root / relative, root, level)
            # Only explicitly checked mappings in the app's private data directory.
            # Startup never discovers or automatically matches the entire audio tree.
            mapping = data / 'audio-maps' / (rec['edition_id'] + '.json')
            if mapping.is_file() and rec['validation'] != 'failed':
                try:
                    rec['audio'] = validate_audio_map(rec, json.loads(mapping.read_text()), root)
                except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as e:
                    rec['audio'] = {'status': 'verification_failed', 'page_map': {}, 'whole_book': None, 'error': str(e)}
            sentence_map = data / 'sentence-maps' / (rec['edition_id'] + '.json')
            if sentence_map.is_file():
                try:
                    from engine.sentence_audio import validate_page_timeline, validate_sentences
                    sentence_mapping = json.loads(sentence_map.read_text())
                    rec['audio']['sentences'] = validate_sentences(rec, sentence_mapping)
                    if rec['audio'].get('whole_book'):
                        timeline = validate_page_timeline(rec, sentence_mapping, rec['audio']['sentences'])
                        if timeline:
                            rec['audio']['whole_book']['timeline'] = timeline
                except (ValueError, KeyError, TypeError, OSError) as e:
                    rec['audio']['sentence_error'] = str(e)
            rich_map = data / 'embedded-maps' / (rec['edition_id'] + '.json')
            if rich_map.is_file():
                try:
                    from engine.embedded_audio import load_embedded_map
                    rec['audio']['page_map'].update(load_embedded_map(rec, rich_map, data))
                    if rec['audio']['page_map']: rec['audio']['status'] = 'verified_page_audio'
                except (ValueError, KeyError, TypeError, OSError) as e:
                    rec['audio']['embedded_error'] = str(e)
            for p in rec['pages']:
                p['audio'] = rec['audio']['page_map'].get(str(p['number']))
            if rec['level'] in set('IJKLMNOPQRSTUVWXYZ') and not rec.get('demo'):
                candidate = candidate_index.get(rec['edition_id'], {})
                audit_file = data / 'i-z-audio-audit' / rec['edition_id'] / 'comparison.json'
                audit_status = None
                if audit_file.is_file():
                    try:
                        audit_status = json.loads(audit_file.read_text()).get('status')
                    except (ValueError, OSError):
                        audit_status = None
                if rec['audio'].get('status', '').startswith('verified_'):
                    candidate_state = 'verified'
                elif audit_status == 'content_mismatch_or_uncertain':
                    candidate_state = 'content_mismatch_or_uncertain'
                elif audit_status == 'insufficient_text':
                    candidate_state = 'insufficient_text'
                elif candidate.get('audio_candidates') or candidate.get('fuzzy_candidates') or candidate.get('cross_level_candidates'):
                    candidate_state = 'filename_candidate_unverified'
                elif rec['audio_detection'].get('rich_media_audio'):
                    candidate_state = 'rich_media_restricted'
                else:
                    candidate_state = 'no_independent_candidate'
                rec['audio']['candidate_state'] = candidate_state
                rec['audio']['candidate_count'] = sum(len(candidate.get(key, [])) for key in
                                                       ('audio_candidates', 'fuzzy_candidates', 'cross_level_candidates'))
            books.append(rec)
            skipped += cached
        except (OSError, ValueError) as e:
            errors.append({'source': relative, 'error': str(e)})
    if (not selection or selection.get('include_demo', False)) and (fixture / 'A Little Garden.pdf').is_file():
        rec, cached = index_pdf(db, fixture / 'A Little Garden.pdf', fixture, 'AA', True)
        skipped += cached
        mapping = fixture / 'page_audio_map.json'
        if mapping.is_file():
            try:
                rec['audio'] = validate_audio_map(rec, json.loads(mapping.read_text()), fixture)
                for p in rec['pages']:
                    p['audio'] = rec['audio']['page_map'].get(str(p['number']))
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as e:
                rec['audio'] = {'status': 'verification_failed', 'page_map': {}, 'whole_book': None, 'error': str(e)}
        books.append(rec)
    # Exact hashes group duplicates without deleting files or merging cross-level titles.
    groups = {}
    for b in books:
        groups.setdefault(b['sha256'], []).append(b['book_id'])
    manifest = {'schema_version': SCHEMA_VERSION, 'resource_root': str(root), 'fixture_root': str(fixture),
                'levels': [{**l, 'prepared': bool(selection and l['level'] in selection['levels'])}
                           for l in discover(root)],
                'scope': selection.get('label') if selection else None, 'books': books, 'errors': errors,
                'exact_duplicate_groups': [g for g in groups.values() if len(g) > 1]}
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    output = data / 'manifest.json'
    if not output.exists() or output.read_text() != serialized:
        temp = data / 'manifest.tmp'
        temp.write_text(serialized)
        temp.replace(output)
    db.close()
    return manifest, skipped


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--data', type=Path, default=APP / 'data')
    args = parser.parse_args()
    result, skipped = build(args.root, args.data)
    print(json.dumps({'books': len(result['books']), 'skipped_metadata': skipped, 'errors': result['errors']}))
