"""Install only the pinned public English→Chinese model, never reading books."""
import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP / 'data/translation'
URL = 'https://argos-net.com/v1/translate-en_zh-1_9.argosmodel'
SHA256 = '433e7c4f034d87fbe2353161e05f18646d7999452f801a4e1f0378522b9850ab'
PACKAGE = 'translate-en_zh-1_9'
FILES = ['metadata.json', 'README.md', 'sentencepiece.model', 'model/model.bin',
         'model/config.json', 'model/shared_vocabulary.json']


def install():
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = ROOT / 'en-zh.argosmodel'
    if not archive.exists():
        with tempfile.NamedTemporaryFile(dir=ROOT, delete=False) as output:
            temporary = Path(output.name)
            try:
                with urllib.request.urlopen(URL, timeout=60) as response:
                    shutil.copyfileobj(response, output)
                output.close()
                if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
                    raise ValueError('Model checksum mismatch')
                temporary.replace(archive)
            finally:
                temporary.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise ValueError('Existing model checksum mismatch; retained for inspection')
    hashes = {}
    with zipfile.ZipFile(archive) as source:
        # Explicit members only: no arbitrary archive paths, no unnecessary Stanza/PyTorch model.
        for name in FILES:
            payload = source.read(PACKAGE + '/' + name)
            target = ROOT / PACKAGE / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or target.read_bytes() != payload:
                target.write_bytes(payload)
            hashes[name] = hashlib.sha256(payload).hexdigest()
    (ROOT / 'SOURCE.json').write_text(json.dumps({
        'url': URL, 'sha256': SHA256, 'package': PACKAGE, 'files': hashes,
        'license': 'CC-BY-4.0 per packaged README',
        'authors': 'Jörg Tiedemann and Santhosh Thottingal',
        'runtime': 'CTranslate2 4.6.0 + SentencePiece 0.2.1; CPU int8',
    }, ensure_ascii=False, indent=2) + '\n')
    print('Local English→Chinese model ready; no reading resources accessed.')


if __name__ == '__main__':
    install()
