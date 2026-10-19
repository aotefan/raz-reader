"""Version-bound local assessment; never accepts an audio path from the browser."""
import json
import re
import subprocess
import tempfile
import threading
import unicodedata
from engine.pipeline import APP
from engine.assessment_process import AssessmentProcess

METHOD = 'openpronounce-0.3.0-weighted-phone-v1'


def reference_text(value):
    if not isinstance(value, str):
        raise ValueError('本页还没有可用于评测的英文文字。')
    value = ' '.join(unicodedata.normalize('NFKC', value).replace('’', "'").split())
    words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", value)
    if not words:
        raise ValueError('本页还没有可用于评测的英文文字。')
    if len(value) > 1500 or len(words) > 120:
        raise ValueError('本页较长，当前评测支持 120 个单词以内的短文。')
    if re.search(r'[<>\x00-\x08\x0e-\x1f]', value):
        raise ValueError('本页文字格式暂不支持评测。')
    return value


class Assessment:
    def __init__(self, data, recordings, timeout=120, remote=None):
        self.directory = data / 'assessment-temp'
        self.recordings = recordings
        self.remote = remote
        self.lock = threading.Lock()
        self.worker = AssessmentProcess([str(APP / '.venv-assessment/bin/python'),
            str(APP / 'scripts/assessment_worker.py'), '--serve'], data, timeout=timeout)

    def warmup(self):
        if not (APP / '.venv-assessment/bin/python').exists() or not (APP / 'data/assessment-model/installed.json').is_file():
            return
        def prepare():
            try:
                self.worker.call({'warmup': True})
            except RuntimeError:
                pass  # Reading still works; an explicit assessment can retry later.
        threading.Thread(target=prepare, name='assessment-warmup', daemon=True).start()

    def close(self):
        if self.remote:
            self.remote.close()
        self.worker.close()

    def run(self, edition, page, version, text, recordings=None):
        recordings = recordings if recordings is not None else self.recordings
        text = reference_text(text)
        if type(version) is not int or version < 1:
            raise ValueError('无效录音版本。')
        snapshot = recordings.assessment_snapshot(edition, page)
        if not snapshot or snapshot[0] != version:
            raise ValueError('录音已更新，请使用最新一次录音。')
        _, audio, previous = snapshot
        if previous:
            previous = json.loads(previous)
            if previous.get('reference') == text and previous.get('method') == METHOD:
                return previous
        result = self.remote.try_run(audio, text) if self.remote else None
        if result is None:
            result = self.local_run(audio, text)
            if self.remote:
                self.remote.record_local()
        if result.get('error'):
            raise ValueError(result['error'])
        result['version'] = version
        if not recordings.save_assessment(edition, page, version, result):
            raise ValueError('录音已更新，请使用最新一次录音。')
        return result

    def local_run(self, audio, text):
        python = APP / '.venv-assessment/bin/python'
        if not python.exists() or not (APP / 'data/assessment-model/installed.json').is_file():
            raise RuntimeError('本机评测模型尚未安装，请运行 scripts/setup_assessment.sh。')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('本机正在评测另一段录音，请稍后重试。')
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.directory) as temporary:
                from pathlib import Path
                path = Path(temporary) / 'recording.audio'
                path.write_bytes(audio)
                return self.worker.call({'path': str(path), 'text': text})
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            raise RuntimeError('本机评测超时或暂不可用，请重试。') from error
        finally:
            self.lock.release()
