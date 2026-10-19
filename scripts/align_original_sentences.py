"""Generate a review candidate, never automatically approve sentence timings.
Inputs: checked page text, word-level whisper.cpp JSON, FFmpeg silence log.
"""
import argparse
import json
import re
from pathlib import Path


def words(text):
    return re.findall(r'[a-z0-9]+', text.lower())


def align(texts, transcription, silence_log, duration):
    tokens = []
    for segment in transcription:
        for word in words(segment['text']):
            tokens.append((word, segment['offsets']['from'] / 1000, segment['offsets']['to'] / 1000))
    spans = []
    start = None
    for line in silence_log.splitlines():
        a = re.search(r'silence_start: ([\d.]+)', line)
        b = re.search(r'silence_end: ([\d.]+)', line)
        if a: start = float(a[1])
        if b and start is not None:
            end = float(b[1])
            if end - start >= .3: spans.append((start, end))
            start = None
    def boundary(time):
        candidates = [(a + b) / 2 for a, b in spans if abs((a + b) / 2 - time) <= .8]
        if not candidates: raise ValueError(f'No safe silence near {time}; manual review required')
        return round(min(candidates, key=lambda x: abs(x-time)), 4)
    pointer, result = 0, []
    for entry in texts:
        target = words(entry['text']); haystack = [t[0] for t in tokens]
        hits = [i for i in range(pointer, len(tokens)-len(target)+1) if haystack[i:i+len(target)] == target]
        if len(hits) != 1: raise ValueError(f'Non-exact or ambiguous transcript match: page {entry["page"]}')
        i = hits[0]; j = i + len(target)
        start = boundary(tokens[i][1]); end = boundary(min(duration, tokens[j-1][2]))
        if start < (result[-1]['end'] if result else 0) or end <= start:
            raise ValueError('Non-monotonic sentence boundaries')
        result.append({**entry, 'start': start, 'end': end, 'clip_text_verified': False})
        pointer = j
    return result

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--text', type=Path, required=True); p.add_argument('--words', type=Path, required=True)
    p.add_argument('--silence', type=Path, required=True); p.add_argument('--duration', type=float, required=True)
    p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    result = align(json.loads(a.text.read_text()), json.loads(a.words.read_text())['transcription'], a.silence.read_text(), a.duration)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(json.dumps({'status': 'clip_review_required', 'sentences': result}, ensure_ascii=False, indent=2))
    print(len(result), 'sentence candidates; each clip must be checked before importing')
