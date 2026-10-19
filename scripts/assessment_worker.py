"""Offline OpenPronounce phone recognition, isolated from the lightweight server.

Uses the upstream public phone APIs, not its online reference-TTS/full-score path.
Score = clipped (1 - weighted phoneme edits / expected phones) * 100.
Reuses upstream posterior/alternate-pronunciation tolerance; practice reference only.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import redirect_stdout
from functools import lru_cache

APP = Path(__file__).resolve().parents[1]
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                  OPENPRONOUNCE_PHONEME_MODEL=str(APP / 'data/assessment-model'),
                  OPENPRONOUNCE_DEVICE='cpu', TOKENIZERS_PARALLELISM='false')
for library in ('/opt/homebrew/lib/libespeak-ng.dylib', '/usr/local/lib/libespeak-ng.dylib'):
    if Path(library).is_file():
        os.environ.setdefault('PHONEMIZER_ESPEAK_LIBRARY', library)
        break


@lru_cache(maxsize=1)
def phone_backend():
    compact = os.environ.get('RAZ_ASSESSMENT_COMPACT')
    if compact:
        sys.path.insert(0, str(APP))
        from engine.compact_assessment import CompactRecognizer
        recognizer = CompactRecognizer(compact)
        return recognizer.decoder, recognizer.recognize
    import openpronounce.phones as phones
    return phones, phones.recognize_phones


def assess(path, text):
    import numpy as np
    import torch
    torch.set_num_threads(int(os.environ.get('RAZ_MODEL_THREADS', '4')))
    decoded = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(path),
                              '-af', 'asetpts=N/SR/TB', '-t', '61', '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'],
                             capture_output=True, timeout=20, check=True)
    samples = np.frombuffer(decoded.stdout, dtype=np.float32).copy()
    duration = len(samples) / 16000
    if duration > 60:
        raise ValueError('这段录音超过 60 秒，请分段练习后再评测。')
    if duration < .4 or not np.isfinite(samples).all():
        raise ValueError('录音太短，请完整读完本页后再试。')
    frames = samples[:len(samples)//320*320].reshape(-1, 320)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    if np.count_nonzero(rms > .003) * .02 < .2:
        raise ValueError('没有听清声音，请靠近麦克风再读一次。')
    if np.mean(np.abs(samples) > .99) > .03:
        raise ValueError('录音声音失真，请离麦克风稍远一点再试。')
    # Trim silence only; do not alter pitch, speed, or pronunciation.
    active = np.flatnonzero(rms > .003)
    samples = samples[max(0, active[0]*320-3200):min(len(samples), (active[-1]+1)*320+3200)]
    phones, recognize = phone_backend()
    recognition = recognize(samples, lang='en')
    if not recognition.phones or float(np.mean(recognition.confidences)) < .3:
        raise ValueError('这次没听清英文，请在安静处再读一次。')
    comparison = phones.compare_phones(recognition, text, lang='en')
    words, groups = phones.get_expected_phones(text, lang='en')
    errors = {item['position']: item for item in comparison['errors']}
    # Pinned upstream internal API: includes accepted reductions and CTC confidence.
    reports = phones._word_reports(recognition, text, lang='en')
    weighted_rate = sum(r['weighted_edits'] for r in reports) / max(1, sum(len(g) for g in groups))
    return {
        'score': round(100 * max(0, 1 - weighted_rate)),
        'method': 'openpronounce-0.3.0-weighted-phone-v1', 'reference_only': True,
        'duration': round(duration, 2), 'reference': text,
        'words': [{'word': word, 'position': i, 'status': 'practice' if i in errors else 'matched',
                   'expected': ''.join(groups[i]), 'heard': errors.get(i, {}).get('actual'),
                   'phones': errors.get(i, {}).get('phones', [])} for i, word in enumerate(words)],
        'phone_error_rate': comparison['phone_error_rate'],
        'weighted_phone_error_rate': round(weighted_rate, 4),
        'heard_phones': comparison['heard_phones'], 'offline': True,
    }


def respond(request):
    try:
        # Keep the JSON protocol separate from third-party import/model messages.
        with redirect_stdout(sys.stderr):
            if request.get('warmup'):
                import numpy as np
                import torch
                torch.set_num_threads(int(os.environ.get('RAZ_MODEL_THREADS', '4')))
                phones, recognize = phone_backend()
                recognize(np.zeros(16000, dtype=np.float32), lang='en')
                if os.environ.get('RAZ_ASSESSMENT_COMPACT'):
                    # Populate the normal short-take execution path before a user arrives.
                    recognize(np.zeros(32000, dtype=np.float32), lang='en')
                phones.get_expected_phones('Ready to read.', lang='en')
                return {'ready': True}
            return assess(request['path'], request['text'])
    except ValueError as error:
        return {'error': str(error)}
    finally:
        if os.environ.get('RAZ_ASSESSMENT_COMPACT') and sys.platform.startswith('linux'):
            # Return temporary activation buffers after long takes; keep model weights.
            import ctypes
            ctypes.CDLL(None).malloc_trim(0)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        # EOF on server exit also terminates the worker: no detached model daemon.
        for line in sys.stdin:
            try:
                result = respond(json.loads(line))
            except Exception as error:
                print(type(error).__name__, file=sys.stderr, flush=True)
                result = {'worker_error': True}
            print(json.dumps(result, ensure_ascii=False), flush=True)
    else:
        print(json.dumps(respond(json.load(sys.stdin)), ensure_ascii=False))
