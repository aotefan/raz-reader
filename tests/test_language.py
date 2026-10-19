import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from engine.language import LanguageTools, english_text, APP


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.language = LanguageTools(self.root)

    @unittest.skipUnless((APP / '.venv-translation/bin/python').exists() and
                         (APP / 'data/translation/translate-en_zh-1_9/model/model.bin').is_file(),
                         'Optional translation model not installed')
    def test_real_offline_model_translates_original_test_sentence(self):
        result = self.language.translate('This is a little garden.')
        self.assertIn('小花园', result['translation'])
        self.assertNotIn('▁', result['translation'])
        self.assertTrue(result['offline'])
        self.assertTrue(result['machine_translation'])

    def test_real_word_voice_is_decodable_and_temporary_files_are_removed(self):
        audio = self.language.pronounce('CITY')
        with wave.open(io.BytesIO(audio)) as stream:
            self.assertGreater(stream.getnframes() / stream.getframerate(), .2)
            self.assertEqual(stream.getnchannels(), 1)
        self.assertEqual(list((self.root / 'speech-temp').iterdir()), [])

    def test_sentence_speech_is_real_audio_and_uses_shared_lock(self):
        audio = self.language.speak('I plant a little seed.')
        with wave.open(io.BytesIO(audio)) as stream:
            self.assertGreater(stream.getnframes() / stream.getframerate(), 1)
        self.assertEqual(list((self.root / 'speech-temp').iterdir()), [])
        with self.language.speech_lock:
            with self.assertRaisesRegex(RuntimeError, '正在准备'):
                self.language.speak('The sun shines.')

    def test_invalid_inputs_cannot_invoke_tools(self):
        with patch('engine.language.subprocess.run') as run:
            for value in [None, 4, '', 'x' * 301, '你好', '<script>alert(1)</script>']:
                with self.assertRaises(ValueError):
                    self.language.translate(value)
                with self.assertRaises(ValueError):
                    self.language.speak(value)
            for value in [None, '', '--help', 'a b', '[[rate 1]]', 'a;touch /tmp/pwn', 'a' * 61]:
                with self.assertRaises(ValueError):
                    self.language.pronounce(value)
            run.assert_not_called()
        self.assertEqual(english_text('  The sun shines.  '), 'The sun shines.')
        self.assertEqual(english_text('A / B & C + 50%'), 'A / B & C + 50%')

    def test_real_book_unicode_punctuation_and_ocr_characters_are_prose(self):
        self.assertEqual(english_text('Lincoln’s views • 50¢ … café [1846]'), 'Lincoln’s views • 50¢ ... café [1846]')
        self.assertEqual(english_text('The\u00a0ofﬁce\u00ad is\u200b open.'), 'The office is open.')
        self.assertEqual(english_text('a non‑breaking hyphen'), 'a non‐breaking hyphen')
        for value in ['Hello\x00world', 'Hello\u202eworld', '[[rate 1]] Hello']:
            with self.assertRaises(ValueError):
                english_text(value)

    def test_busy_model_fails_promptly_and_failed_process_releases_lock(self):
        with patch('engine.language.Path.exists', return_value=True), patch('engine.language.Path.is_file', return_value=True):
            with self.language.translation_lock:
                with self.assertRaisesRegex(RuntimeError, '正在翻译'):
                    self.language.translate('Hello.')
            with patch('engine.language.subprocess.run', side_effect=OSError('Unavailable')):
                with self.assertRaises(RuntimeError):
                    self.language.translate('Hello.')
        self.assertFalse(self.language.translation_lock.locked())
        with patch('engine.language.subprocess.run', side_effect=OSError('Unavailable')):
            with self.assertRaises(RuntimeError):
                self.language.pronounce('city')
        self.assertFalse(self.language.speech_lock.locked())
        self.assertEqual(list((self.root / 'speech-temp').iterdir()), [])
