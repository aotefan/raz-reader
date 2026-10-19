"""Bounded single short-text inference. Invoked via stdin, no text in argv or logs."""
import json
import os
import sys
from pathlib import Path


def offline(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind'):
        raise PermissionError('Translation worker is offline')


sys.addaudithook(offline)
if os.environ.get('RAZ_TRANSLATION_NATIVE_ONLY') == '1':
    # The pinned cloud wheel eagerly imports model converters and their Torch /
    # Transformers dependencies. In this isolated inference worker only the
    # compiled Translator API is used; no model conversion or extensions run.
    import importlib.util
    import types
    spec = importlib.util.find_spec('ctranslate2')
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError('The pinned CTranslate2 runtime is not installed')
    package = types.ModuleType('ctranslate2')
    package.__path__ = list(spec.submodule_search_locations)
    package.__package__ = 'ctranslate2'
    sys.modules['ctranslate2'] = package
    from ctranslate2._ext import Translator
else:
    from ctranslate2 import Translator
import sentencepiece

root = Path(__file__).resolve().parents[1] / 'data/translation/translate-en_zh-1_9'
body = json.loads(sys.stdin.read(4096))
processor = sentencepiece.SentencePieceProcessor(model_file=str(root / 'sentencepiece.model'))
tokens = processor.encode(body['text'], out_type=str)
if len(tokens) > 256:
    print(json.dumps({'error': '文字分词后过长，请拆成更短的句子。'}))
    sys.exit(0)
translator = Translator(str(root / 'model'), device='cpu', compute_type='int8',
                        inter_threads=1, intra_threads=2)
result = translator.translate_batch([tokens], beam_size=4, replace_unknowns=True,
                                    max_input_length=256, max_decoding_length=512)[0]
# Argos tokenizer also removes residual SentencePiece whitespace markers for Asian output.
translated = processor.decode(result.hypotheses[0]).replace('▁', ' ').strip()
print(json.dumps({'translation': translated}, ensure_ascii=False))
