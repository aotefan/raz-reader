"""Download the pinned public phoneme model once; never sends reading/voice data."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

APP = Path(__file__).resolve().parents[1]
REPO = 'facebook/wav2vec2-lv-60-espeak-cv-ft'
REVISION = 'ae45363bf3413b374fecd9dc8bc1df0e24c3b7f4'
FILES = ['README.md', 'config.json', 'preprocessor_config.json', 'pytorch_model.bin',
         'special_tokens_map.json', 'tokenizer_config.json', 'vocab.json']


def install():
    destination = APP / 'data/assessment-model'
    destination.mkdir(parents=True, exist_ok=True)
    metadata = json.load(urllib.request.urlopen(
        f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true', timeout=30))
    expected = {f['rfilename']: f.get('lfs', {}).get('sha256') for f in metadata['siblings']}
    def fetch(name):
        path = destination / name
        if not path.exists():
            temporary = path.with_suffix(path.suffix + '.partial')
            with urllib.request.urlopen(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}', timeout=120) as response, temporary.open('wb') as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block: break
                    output.write(block)
            temporary.replace(path)
        hasher = hashlib.sha256()
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                hasher.update(block)
        checksum = hasher.hexdigest()
        if expected.get(name) and checksum != expected[name]:
            raise RuntimeError(f'Model checksum mismatch: {name}')
        print(name, path.stat().st_size, flush=True)
        return name, checksum
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        checksums = dict(pool.map(fetch, FILES))
    (destination / 'installed.json').write_text(json.dumps({
        'repo': REPO, 'revision': REVISION, 'license': 'Apache-2.0', 'sha256': checksums}, indent=2))


if __name__ == '__main__':
    install()
