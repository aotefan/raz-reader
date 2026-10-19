"""Validated time slices of original narration. No voice synthesis."""
import math
import re
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from engine.pipeline import contained, digest


def validate_sentences(book, mapping):
    if not isinstance(mapping, dict):
        raise ValueError('Sentence mapping must be an object')
    whole = book['audio'].get('whole_book')
    evidence = mapping.get('evidence')
    verified_manual = (mapping.get('verification') == 'local_text_asr_silence_clip_checked'
                       and isinstance(evidence, dict)
                       and all(re.fullmatch(r'[0-9a-f]{64}', str(evidence.get(k, '')))
                               for k in ('text_sha256', 'words_sha256', 'silence_sha256', 'clip_review_sha256')))
    verified_alignment = (mapping.get('verification') == 'whisperx_forced_alignment_pdf_match'
                          and isinstance(evidence, dict)
                          and evidence.get('method') == 'WhisperX wav2vec2 forced alignment'
                          and mapping.get('source_pdf_sha256') == book['sha256']
                          and evidence.get('pdf_sha256') == book['sha256']
                          and evidence.get('audio_sha256') == whole['sha256']
                          and isinstance(evidence.get('model'), str) and evidence['model'].strip()
                          and all(re.fullmatch(r'[0-9a-f]{64}', str(evidence.get(k, '')))
                                  for k in ('text_sha256', 'words_sha256', 'alignment_sha256')))
    if (not whole or mapping.get('edition_id') != book['edition_id']
            or mapping.get('audio_sha256') != whole['sha256']
            or not (verified_manual or verified_alignment)):
        raise ValueError('Sentence mapping requires exact PDF/audio and checked text/timing evidence')
    sentences = mapping.get('sentences')
    if not isinstance(sentences, list) or not sentences:
        raise ValueError('Empty sentence mapping')
    result, previous_end, previous_page = [], 0, 0
    seen = set()
    for entry in sentences:
        if not isinstance(entry, dict):
            raise ValueError('Sentence entry must be an object')
        page, start, end, text = (entry.get(k) for k in ('page', 'start', 'end', 'text'))
        if (type(page) is not int or not previous_page <= page <= book['page_count'] or page < 1
                or not isinstance(text, str) or not text.strip() or len(text) > 1000
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or start < previous_end or not .05 <= end - start <= 60 or end > whole['duration']
                or (verified_manual and entry.get('clip_text_verified') is not True)):
            raise ValueError('Invalid sentence text, page or audio boundaries')
        normalized = ' '.join(re.findall(r'[a-z0-9]+', text.lower()))
        if not normalized or (page, normalized) in seen:
            raise ValueError('Ambiguous sentence on the same page')
        scores = []
        if verified_alignment:
            aligned = entry.get('word_alignment')
            if not isinstance(aligned, list) or not aligned:
                raise ValueError('Forced-aligned sentence must include word timings')
            aligned_tokens, last_word_start = [], start
            for word in aligned:
                if not isinstance(word, dict) or not isinstance(word.get('text'), str):
                    raise ValueError('Invalid forced-aligned word')
                word_start, word_end, score = word.get('start'), word.get('end'), word.get('score')
                if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (word_start, word_end, score))
                        or word_start < last_word_start - .02 or word_end <= word_start
                        or word_start < start - .05 or word_end > end + .05 or not 0 <= score <= 1):
                    raise ValueError('Invalid forced-aligned word timing or confidence')
                aligned_tokens.extend(re.findall(r'[a-z0-9]+', word['text'].lower()))
                last_word_start = word_start; scores.append(score)
            alignment_normalized = ' '.join(re.findall(r'[a-z0-9]+', re.sub(r"['’]", '', text.lower())))
            if ' '.join(aligned_tokens) != alignment_normalized or sum(scores) / len(scores) < .45:
                raise ValueError('Forced alignment does not exactly cover the checked sentence')
        elif entry.get('clip_text_verified') is not True:
            raise ValueError('Sentence clip was not checked')
        seen.add((page, normalized))
        result.append({'id': str(len(result) + 1), 'page': page, 'start': start, 'end': end, 'text': text,
                       **({'alignment_score': round(sum(scores) / len(scores), 3)} if scores else {})})
        previous_page, previous_end = page, end
    if verified_alignment:
        texts = '\n'.join(item['text'] for item in sentences)
        words = json.dumps([item['word_alignment'] for item in sentences], ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))
        spans = json.dumps([{k: item[k] for k in ('page', 'start', 'end', 'text')} for item in sentences],
                           ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        checks = {'text_sha256': hashlib.sha256(texts.encode()).hexdigest(),
                  'words_sha256': hashlib.sha256(words.encode()).hexdigest(),
                  'alignment_sha256': hashlib.sha256(spans.encode()).hexdigest()}
        if any(evidence.get(key) != value for key, value in checks.items()):
            raise ValueError('Forced-alignment evidence digest mismatch')
    return result


def validate_page_timeline(book, mapping, sentences):
    """Create sparse page turns directly from verified sentence slices."""
    if mapping.get('verification') not in ('local_text_asr_silence_clip_checked',
                                            'whisperx_forced_alignment_pdf_match'):
        raise ValueError('Page timeline requires a verified sentence map')
    timeline = []
    for sentence in sentences:
        if timeline and timeline[-1]['page'] == sentence['page']:
            timeline[-1]['end'] = sentence['end']
        else:
            timeline.append({'page': sentence['page'], 'start': sentence['start'], 'end': sentence['end']})
    if any(cue['page'] > book['page_count'] for cue in timeline):
        raise ValueError('Page timeline exceeds this edition')
    return timeline


def render_original_clip(book, sentence, data):
    asset = book['audio']['whole_book']
    path = contained(Path(book['source_root']) / asset['file'], book['source_root'])
    if digest(path) != asset['sha256']:
        raise ValueError('Original audio changed; sentence mapping is no longer valid')
    root = Path(data) / 'sentence-temp'; root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temp:
        output = Path(temp) / 'clip.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-xerror', '-nostdin', '-i', str(path),
                        '-ss', str(sentence['start']), '-t', str(sentence['end'] - sentence['start']),
                        '-vn', '-acodec', 'pcm_s16le', str(output)],
                       capture_output=True, check=True, timeout=45)
        return output.read_bytes()
