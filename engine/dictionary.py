"""Offline word meanings and phonetics from the pinned ECDICT corpus."""
import re
import sqlite3


def lookup(path, word):
    word = word.strip().lower().replace('’', "'")
    if not re.fullmatch(r"[a-z][a-z'\-]{0,59}", word):
        raise ValueError('请输入一个英文单词')
    if not path.is_file():
        return {'available': False, 'word': word, 'found': False}
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        row = db.execute('SELECT word,phonetic,translation,exchange FROM entries WHERE word=?', (word,)).fetchone()
        if not row:
            return {'available': True, 'word': word, 'found': False}
        # Preserve source spelling/phonetics; never fabricate missing IPA or meanings.
        return {'available': True, 'found': True, 'word': row[0], 'phonetic': row[1],
                'meanings': row[2].split('\\n') if row[2] else [], 'exchange': row[3], 'source': 'ECDICT'}
    finally:
        db.close()
