"""Read original MP3 assets referenced by RichMedia; never run SWF content."""
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from engine.pipeline import contained, digest, run


def prepare_embedded(book, data):
    root = Path(data).resolve()
    records = book.get('audio_detection', {}).get('rich_media_audio', [])
    target = root / 'embedded-maps' / (book['edition_id'] + '.json')
    if target.exists():
        load_embedded_map(book, target, root)
        return
    if digest(book['source_path']) != book['sha256']: raise ValueError('PDF changed')
    assets, seen, failures = [], {}, []
    for record in records:
        ref = record['stream_ref']
        if not re.fullmatch(r'\d+ \d+ R', ref): raise ValueError('Invalid stream reference')
        try:
            if ref not in seen:
                output = root / 'embedded-audio' / book['edition_id'] / (ref.replace(' ', '_') + '.mp3')
                output.parent.mkdir(parents=True, exist_ok=True)
                command = ['qpdf', '--show-object=' + ','.join(ref.split()[:2]), '--filtered-stream-data', book['source_path']]
                result = subprocess.run(command, capture_output=True, timeout=60)
                if result.returncode not in (0, 3) or not result.stdout: raise ValueError('Stream extraction failed')
                # At most one original media payload, never the SWF player.
                output.write_bytes(result.stdout)
                probe = json.loads(run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)]))
                if not any(s.get('codec_name') == 'mp3' for s in probe['streams']): raise ValueError('Not MP3')
                duration = float(probe['format']['duration'])
                if not math.isfinite(duration) or duration <= 0: raise ValueError('Empty audio')
                run(['ffmpeg','-v','error','-xerror','-nostdin','-i',str(output),'-f','null','-'])
                seen[ref] = {'file': str(output.relative_to(root)), 'sha256': digest(output), 'duration': duration,
                             'stream_ref': ref, 'verification': 'original_pdf_page_association_full_decode'}
            assets.append({**seen[ref], 'page': record['page']})
        except (ValueError, OSError, subprocess.SubprocessError, KeyError) as error:
            failures.append({'page':record['page'], 'stream_ref':ref, 'error':str(error)[:300]})
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps({'edition_id':book['edition_id'], 'assets':assets,'failures':failures},ensure_ascii=False,indent=2))
    temp.replace(target)


def load_embedded_map(book, path, data):
    mapping = json.loads(Path(path).read_text())
    if mapping['edition_id'] != book['edition_id']: raise ValueError('Wrong PDF edition')
    allowed = {(r['page'], r['stream_ref']) for r in book['audio_detection'].get('rich_media_audio', [])}
    grouped = defaultdict(list)
    for a in mapping['assets']:
        if (a['page'], a['stream_ref']) not in allowed: raise ValueError('Unmapped page/stream')
        source = contained(Path(data) / a['file'], Path(data) / 'embedded-audio' / book['edition_id'])
        duration = a['duration']
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('Invalid duration')
        if digest(source) != a['sha256']: raise ValueError('Changed embedded audio')
        grouped[str(a['page'])].append({**a, 'asset_id':'sha256:'+a['sha256'], 'storage':'app_embedded'})
    # Ambiguous multiple clips on one page must not be silently merged/chosen.
    return {page: entries[0] for page, entries in grouped.items() if len({a['sha256'] for a in entries}) == 1}
