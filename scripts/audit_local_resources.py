"""Read-only local inventory. No import, deletion, audio extraction or conversion."""
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.pipeline import APP, DEFAULT_ROOT, inspect_pdf


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def audit(root=DEFAULT_ROOT, output=APP / 'data/resource-review'):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.is_relative_to(root):
        raise ValueError('Audit output must be outside original resources')
    output.mkdir(parents=True, exist_ok=True)
    cache_path = output / 'cache.json'
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    paths = sorted(p for p in root.rglob('*') if p.is_file() and not p.is_symlink()
                   and p.resolve().is_relative_to(root))

    def inspect(path):
        rel = path.relative_to(root).as_posix()
        stat = path.stat()
        fingerprint = [stat.st_size, stat.st_mtime_ns]
        if cache.get(rel, {}).get('fingerprint') == fingerprint:
            return cache[rel]
        r = dict(path=rel, bytes=stat.st_size, fingerprint=fingerprint, type=path.suffix.lower(), status='inventory_only')
        try:
            if r['type'] in ('.pdf', '.mp3', '.rar'):
                r['sha256'] = sha256(path)
            if r['type'] == '.pdf':
                pdf = inspect_pdf(path)
                r.update(page_count=pdf['page_count'], embedded_streams=pdf['audio_detection']['embedded_stream_count'],
                         audio_pages=sorted({i for a in pdf['audio_detection']['screen_annotations'] for i in a['pages']}),
                         status='structure_readable', audio_verified=False, layout_verified=False)
            elif r['type'] == '.mp3':
                p = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                   capture_output=True, text=True, timeout=30)
                if p.returncode:
                    raise ValueError(p.stderr[:400])
                probe = json.loads(p.stdout)
                r.update(status='format_readable', duration=probe.get('format', {}).get('duration'),
                         codecs=[s.get('codec_name') for s in probe.get('streams', []) if s.get('codec_type') == 'audio'],
                         full_decode_verified=False, book_match_verified=False)
                if not r['codecs']:
                    raise ValueError('No audio stream found')
            elif r['type'] == '.rar':
                p = subprocess.run(['bsdtar', '-tf', str(path)], capture_output=True, text=True, timeout=120)
                if p.returncode:
                    raise ValueError(p.stderr[:400])
                entries = p.stdout.splitlines()
                r.update(status='directory_readable', member_count=len(entries),
                         member_types=dict(Counter(Path(x).suffix.lower() or '(directory/no suffix)' for x in entries)),
                         full_integrity_verified=False)
                (output / (r['sha256'] + '.entries.txt')).write_text(p.stdout)
            after = path.stat()
            if [after.st_size, after.st_mtime_ns] != fingerprint:
                raise ValueError('Changed during audit; retry after download/extraction finishes')
        except Exception as error:
            r.update(status='inspection_failed', error=str(error)[:500])
        return r

    records = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for n, future in enumerate(as_completed([pool.submit(inspect, p) for p in paths]), 1):
            r = future.result(); records.append(r)
            if n % 100 == 0:
                print(f'Inspected {n}/{len(paths)}', flush=True)
    records.sort(key=lambda r: r['path'])
    cache_path.write_text(json.dumps({r['path']: r for r in records}, ensure_ascii=False, indent=2))
    fields = ['path', 'bytes', 'type', 'sha256', 'status', 'page_count', 'embedded_streams', 'audio_pages',
              'duration', 'codecs', 'member_count', 'error']
    with (output / 'inventory.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); w.writeheader(); w.writerows(records)
    groups = defaultdict(list)
    for r in records:
        if r.get('sha256') and r['status'] != 'inspection_failed': groups[r['sha256']].append(r)
    def priority(r):
        path = r['path']
        # Prefer active original top-level PDFs and original audio directory; never delete here.
        return (0 if len(Path(path).parts) == 2 and r['type'] == '.pdf' else
                1 if path.startswith('音频/') else 2, path)
    duplicates = []
    for digest, group in groups.items():
        if len(group) < 2: continue
        ordered = sorted(group, key=priority)
        for extra in ordered[1:]:
            duplicates.append(dict(keep=ordered[0]['path'], duplicate=extra['path'], bytes=extra['bytes'], sha256=digest))
    with (output / 'exact_duplicates.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['keep', 'duplicate', 'bytes', 'sha256']); w.writeheader(); w.writerows(duplicates)
    summary = {}
    for r in records:
        top = r['path'].split('/')[0]
        s = summary.setdefault(top, dict(files=0, bytes=0, pdf=0, mp3=0, rar=0, embedded_pdf=0, failures=[]))
        s['files'] += 1; s['bytes'] += r['bytes']
        if r['type'] in ('.pdf', '.mp3', '.rar'): s[r['type'][1:]] += 1
        if r.get('embedded_streams', 0): s['embedded_pdf'] += 1
        if r['status'] == 'inspection_failed': s['failures'].append(r['path'])
    result = dict(root=str(root), total_files=len(records), categories=dict(Counter(r['type'] for r in records)),
                  summary=summary, duplicate_extra_files=len(duplicates), duplicate_extra_bytes=sum(r['bytes'] for r in duplicates),
                  failures=[r for r in records if r['status'] == 'inspection_failed'],
                  limits='Metadata/structure/format only, not whole-library render, full audio decode, content match or archive CRC validation')
    (output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: result[k] for k in ['total_files', 'categories', 'duplicate_extra_files', 'duplicate_extra_bytes']}, ensure_ascii=False))
    return result

if __name__ == '__main__':
    audit()
