"""Self-authored test story, illustrations and exact per-page spoken transcripts.

System TTS generates local test speech; no external book or recording is read.
Original text/art may be used as examples under the project license. Generated
voice files remain ignored and are not distributed by this repository.
"""
import json
import subprocess
import sys
import tempfile
import wave
import shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reportlab.pdfgen import canvas
from engine.pipeline import APP, digest

TEXT = ["A Little Garden. Let's read together.", "I plant a little seed.", "The sun shines. My flower grows."]


def ensure_whole_book(root):
    mapping_path = root / 'page_audio_map.json'
    mapping = json.loads(mapping_path.read_text())
    if mapping.get('whole_book', {}).get('timeline_verification') == 'self_authored_pcm_concatenation_checked':
        return
    text = ' '.join(TEXT)
    # Only this project's authorized fixture: concatenate decoded page samples,
    # not independently synthesized durations or guessed silence boundaries.
    mp3 = root / 'whole-book-timed.mp3'
    timeline, frames = [], 0
    with tempfile.TemporaryDirectory(dir=root) as temp:
        combined = Path(temp) / 'combined.wav'
        with wave.open(str(combined), 'wb') as out:
            out.setparams((1, 2, 44100, 0, 'NONE', 'not compressed'))
            for number, entry in enumerate(mapping['pages'], 1):
                source = root / entry['file']
                if entry['page'] != number or entry['transcript'] != TEXT[number-1] or digest(source) != entry['sha256']:
                    raise ValueError('Fixture page transcript or audio hash changed')
                decoded = Path(temp) / f'{number}.wav'
                subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(source), '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', str(decoded)], check=True)
                with wave.open(str(decoded), 'rb') as audio:
                    count = audio.getnframes()
                    out.writeframes(audio.readframes(count))
                timeline.append({'page': number, 'start': frames / 44100, 'end': (frames + count) / 44100,
                                 'source_sha256': entry['sha256']})
                frames += count
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(combined), '-codec:a', 'libmp3lame', '-q:a', '4', str(mp3)], check=True)
    mapping['whole_book'] = {'file': mp3.name, 'sha256': digest(mp3), 'transcript': text,
                             'verification': 'self_authored_transcript_and_book_checked',
                             'timeline': timeline, 'timeline_verification': 'self_authored_pcm_concatenation_checked',
                             'timeline_provenance': 'Concatenated verified self-authored page audio as 44100 Hz mono PCM; boundaries counted in decoded samples before a single MP3 encode.'}
    temporary = root / 'page_audio_map.tmp'
    temporary.write_text(json.dumps(mapping, indent=2) + '\n')
    temporary.replace(mapping_path)
    print('Added whole-book test narration with sample-counted page boundaries.')


def make():
    root = APP / 'fixtures/generated'
    if (root / 'page_audio_map.json').is_file():
        ensure_whole_book(root)
        print('Existing PDF and page audio retained.')
        return
    root.mkdir(parents=True, exist_ok=True)
    pdf = root / 'A Little Garden.pdf'
    c = canvas.Canvas(str(pdf), pagesize=(600, 760), invariant=1)
    c.setTitle('A Little Garden'); c.setAuthor('RAZ Reader contributors — original example')
    for i, text in enumerate(TEXT):
        c.setFillColorRGB(.95, .96, .89); c.rect(0, 0, 600, 760, fill=1, stroke=0)
        c.setFillColorRGB(.24, .40, .29); c.setFont('Helvetica', 12)
        c.drawCentredString(300, 710, 'SELF-AUTHORED TEST BOOK  /  LEVEL AA')
        c.setFont('Helvetica-Bold', 34 if i == 0 else 28)
        c.drawCentredString(300, 625, 'A Little Garden' if i == 0 else text.split('. ')[0] + ('.' if i == 2 else ''))
        if i == 0:
            c.setFont('Helvetica', 18); c.drawCentredString(300, 580, "Let's read together.")
        if i == 2:
            c.setFont('Helvetica-Bold', 28); c.drawCentredString(300, 575, 'My flower grows.')
        c.setFillColorRGB(.85, .69, .43); c.roundRect(135, 170, 330, 105, 30, fill=1, stroke=0)
        c.setStrokeColorRGB(.36, .55, .31); c.setLineWidth(13)
        c.line(300, 230, 300, 365 if i == 1 else 420)
        c.setFillColorRGB(.46, .64, .36)
        c.ellipse(215, 300, 299, 347, fill=1, stroke=0); c.ellipse(300, 330, 385, 377, fill=1, stroke=0)
        if i != 1:
            c.setFillColorRGB(.91, .58, .39)
            for x, y in [(300, 465), (340, 435), (325, 390), (275, 390), (260, 435)]:
                c.circle(x, y, 34, fill=1, stroke=0)
            c.setFillColorRGB(.99, .79, .35); c.circle(300, 430, 27, fill=1, stroke=0)
        c.setFillColorRGB(.97, .80, .40); c.circle(475, 485, 40, fill=1, stroke=0)
        c.setFillColorRGB(.43, .51, .39); c.setFont('Helvetica', 12)
        c.drawCentredString(300, 80, 'Original local demo. This is not a RAZ publication.')
        c.drawCentredString(300, 55, str(i + 1)); c.showPage()
    c.save()
    pages = []
    for i, text in enumerate(TEXT, 1):
        aiff, mp3 = root / f'page-{i}.aiff', root / f'page-{i}.mp3'
        if sys.platform == 'darwin':
            subprocess.run(['/usr/bin/say', '-v', 'Samantha', '-r', '125', '-o', str(aiff), text], check=True)
        elif shutil.which('espeak-ng'):
            subprocess.run(['espeak-ng', '-v', 'en-us', '-s', '125', '-w', str(aiff), text], check=True)
        else:
            raise RuntimeError('Install espeak-ng to generate the local demo narration.')
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(aiff), '-codec:a', 'libmp3lame', '-q:a', '4', str(mp3)], check=True)
        pages.append({'page': i, 'file': mp3.name, 'sha256': digest(mp3), 'transcript': text})
    mapping = {'edition_id': 'pdf_' + digest(pdf), 'verification': 'self_authored_transcript_and_page_checked',
               'provenance': 'Original story and vector art by this project. Exact page transcript synthesized locally by the installed system TTS. Generated speech is excluded from source distribution.',
               'pages': pages}
    (root / 'page_audio_map.json').write_text(json.dumps(mapping, indent=2) + '\n')
    ensure_whole_book(root)
    print('Created self-authored 3-page PDF and 3 offline synthesized MP3 tracks.')


if __name__ == '__main__':
    make()
