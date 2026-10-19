"""Install the pinned, public ECDICT corpus locally. No book content is sent anywhere."""
import csv
import hashlib
import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b'
BASE = f'https://raw.githubusercontent.com/skywind3000/ECDICT/{COMMIT}/'
EXPECTED_SHA256 = '1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf'


def install():
    target = ROOT / 'data/dictionary'
    target.mkdir(parents=True, exist_ok=True)
    destination = target / 'ecdict.sqlite3'
    if destination.exists():
        print('Existing local dictionary retained.'); return
    source = target / 'ecdict.csv'
    if not source.exists():
        temporary = target / 'download.tmp'
        with urllib.request.urlopen(BASE + 'ecdict.csv', timeout=60) as response, temporary.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        temporary.replace(source)
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if sha != EXPECTED_SHA256:
        raise ValueError('Pinned ECDICT checksum mismatch; dictionary not installed')
    license_path = ROOT / 'THIRD_PARTY_LICENSES/ECDICT-MIT.txt'
    if not license_path.exists():
        with urllib.request.urlopen(BASE + 'LICENSE', timeout=30) as response:
            license_path.write_bytes(response.read())
    temporary = target / 'dictionary.tmp.sqlite3'
    db = sqlite3.connect(temporary)
    try:
        db.execute('DROP TABLE IF EXISTS entries')
        db.execute('CREATE TABLE entries (word TEXT PRIMARY KEY COLLATE NOCASE, phonetic TEXT, translation TEXT, exchange TEXT)')
        with source.open(encoding='utf-8-sig', newline='') as stream:
            rows = csv.DictReader(stream)
            db.executemany('INSERT OR IGNORE INTO entries VALUES (?,?,?,?)',
                           ((r['word'], r['phonetic'], r['translation'], r['exchange']) for r in rows if r['word']))
        count = db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
        db.commit()
    finally:
        db.close()
    temporary.replace(destination)
    metadata = {'source': 'https://github.com/skywind3000/ECDICT', 'commit': COMMIT, 'license': 'MIT (upstream declaration)',
                'sha256': sha, 'entries': count}
    (target / 'SOURCE.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'Installed {count} ECDICT entries locally.')


if __name__ == '__main__':
    install()
