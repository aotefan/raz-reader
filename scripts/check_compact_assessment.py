"""Compare original and compact execution on self-authored audio, never user takes."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import wave

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from engine.assessment_process import AssessmentProcess


def fixtures(directory):
    directory.mkdir(parents=True, exist_ok=True)
    source = APP / 'data/assessment-fixtures'
    reference = 'I plant a little seed.'
    cases = [
        ('correct', source / 'correct.wav', reference),
        ('wrong', source / 'wrong.wav', reference),
        ('omitted', source / 'correct.wav', reference + ' My flower grows.'),
        ('city_wrong', source / 'wrong.wav', 'The people.'),
        ('silence', source / 'silence.wav', reference),
    ]
    with wave.open(str(source / 'correct.wav')) as audio:
        params = audio.getparams()
        raw = audio.readframes(audio.getnframes())
    for repeats in [8, 20]:
        path = directory / f'repeated-{repeats}.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setparams(params)
            audio.writeframes(raw * repeats)
        cases.append((f'repeated_{repeats}', path, ' '.join([reference] * repeats)))
    slow = directory / 'long-55.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(directory / 'repeated-20.wav'),
                    '-af', 'atempo=0.59', str(slow)], check=True)
    cases.append(('long_55', slow, ' '.join([reference] * 20)))
    for name, filter_value in [('quiet', 'volume=0.15'), ('fast', 'atempo=1.35'),
                               ('slow', 'atempo=0.75')]:
        path = directory / f'{name}.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source / 'correct.wav'),
                        '-af', filter_value, str(path)], check=True)
        cases.append((name, path, reference))
    return cases


def run(output):
    cases = fixtures(output / 'fixtures')
    results = {}
    command = [str(APP / '.venv-assessment/bin/python'), str(APP / 'scripts/assessment_worker.py'), '--serve']
    for mode in ['original', 'compact']:
        os.environ.pop('RAZ_ASSESSMENT_COMPACT', None)
        if mode == 'compact':
            os.environ['RAZ_ASSESSMENT_COMPACT'] = str(APP / 'data/assessment-model-compact')
        worker = AssessmentProcess(command, output / mode, timeout=180)
        measured = {}
        try:
            start = time.monotonic()
            assert worker.call({'warmup': True}) == {'ready': True}
            print(mode, 'warmup', round(time.monotonic() - start, 3), flush=True)
            for name, path, reference in cases:
                start = time.monotonic()
                result = worker.call({'path': str(path), 'text': reference})
                measured[name] = {'seconds': round(time.monotonic() - start, 3), 'result': result}
                print(mode, name, measured[name]['seconds'], result.get('score', result.get('error')), flush=True)
                if mode == 'compact':
                    equal = result == results['original'][name]['result']
                    measured[name]['full_report_equal'] = equal
                    print('full_report_equal', equal, flush=True)
        finally:
            worker.close()
        results[mode] = measured
    (output / 'parity.json').write_text(json.dumps(results, ensure_ascii=False, indent=2))
    assert all(item['full_report_equal'] for item in results['compact'].values()), 'Full reports changed; inspect parity.json'
    print('PASS: every full score/word report/error matches the original', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=APP / 'data/research/compact-assessment')
    run(parser.parse_args().output)
