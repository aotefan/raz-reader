"""Offline short English text translation and macOS English speech."""
import json
import sys
import re
import subprocess
import tempfile
import threading
import unicodedata
from pathlib import Path
from engine.pipeline import APP


def english_text(value):
    if not isinstance(value, str):
        raise ValueError('请输入英文句子')
    value = unicodedata.normalize('NFKC', value)
    value = value.translate({ord(c): None for c in '\u00ad\u200b\ufeff'}).strip()
    if not 1 <= len(value) <= 300 or not re.search('[A-Za-z]', value):
        raise ValueError('请输入 1–300 个字符的英文句子')
    # OCR and real books contain Unicode quotes, currency signs, bullets,
    # accented names and ligatures. These are prose, not invalid API input.
    if any(unicodedata.category(c)[0] == 'C' and c not in '\n\r\t' for c in value) or re.search(r'[<>]|\[\[', value):
        raise ValueError('文字中包含无法处理的控制字符或标记，请重试。')
    return value


class LanguageTools:
    def __init__(self, data):
        self.data = Path(data)
        self.translation_lock = threading.Lock()
        self.speech_lock = threading.Lock()

    def translate(self, value):
        value = english_text(value)
        python = APP / '.venv-translation/bin/python'
        model = APP / 'data/translation/translate-en_zh-1_9/model/model.bin'
        if not python.exists() or not model.is_file():
            raise RuntimeError('本机翻译模型尚未安装，请按 README 安装离线翻译。')
        if not self.translation_lock.acquire(blocking=False):
            raise RuntimeError('本机正在翻译，请稍后再试。')
        try:
            process = subprocess.run([str(python), str(APP / 'scripts/translation_worker.py')],
                                     input=json.dumps({'text': value}), capture_output=True,
                                     text=True, timeout=45, cwd=APP)
            if process.returncode:
                raise RuntimeError('本机翻译暂不可用，请检查模型安装。')
            result = json.loads(process.stdout)
            if result.get('error'):
                raise ValueError(result['error'])
            if not result.get('translation'):
                raise RuntimeError('没有生成译文，请缩短句子后重试。')
            return {**result, 'source': 'Argos en→zh 1.9 / OPUS-MT', 'offline': True,
                    'machine_translation': True}
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise RuntimeError('本机翻译超时或暂不可用，请重试。') from error
        finally:
            self.translation_lock.release()

    def pronounce(self, word):
        if not isinstance(word, str):
            raise ValueError('请输入一个英文单词')
        word = word.strip().lower().replace('’', "'")
        if not re.fullmatch(r"[a-z][a-z'\-]{0,59}", word):
            raise ValueError('请输入一个英文单词')
        return self._speech(word)

    def speak(self, text):
        return self._speech(english_text(text))

    def _speech(self, text):
        if not self.speech_lock.acquire(blocking=False):
            raise RuntimeError('正在准备发音，请稍后再试。')
        try:
            directory = self.data / 'speech-temp'
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=directory) as temporary:
                root = Path(temporary)
                (root / 'word.txt').write_text(text)
                source = root / 'word.aiff'
                if sys.platform == 'darwin':
                    command = ['/usr/bin/say', '-v', 'Samantha', '-r', '130',
                               '-f', str(root / 'word.txt'), '-o', str(source)]
                else:
                    source = root / 'generated.wav'
                    command = ['espeak-ng', '-v', 'en-us', '-s', '130',
                               '-f', str(root / 'word.txt'), '-w', str(source)]
                subprocess.run(command, capture_output=True, check=True, timeout=15)
                subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(source),
                                '-ar', '22050', '-ac', '1', str(root / 'word.wav')],
                               capture_output=True, check=True, timeout=15)
                return (root / 'word.wav').read_bytes()
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError('单词发音暂不可用，请稍后重试。') from error
        finally:
            self.speech_lock.release()
