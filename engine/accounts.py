"""Small, private account store. Passwords use salted PBKDF2-SHA256, never plaintext.

Accounts own an existing private visitor directory; claiming it revokes anonymous
access. Login sessions are random, revocable, and stored only as SHA-256 digests.
"""
from contextlib import contextmanager
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import unicodedata


class Accounts:
    def __init__(self, root):
        self.path = Path(root) / 'accounts.sqlite3'
        self.hash_slot = threading.BoundedSemaphore(1)
        with self.connection() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY, directory TEXT NOT NULL UNIQUE,
                salt BLOB NOT NULL, password BLOB NOT NULL, level TEXT NOT NULL DEFAULT 'all');
              CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, username TEXT NOT NULL, expires INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
            ''')
            if 'display_name' not in {row[1] for row in db.execute('PRAGMA table_info(users)')}:
                db.execute("ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''")
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def credentials(username, password):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_\-]{3,32}', username):
            raise ValueError('用户名用 3–32 位字母、数字、下划线或短横线。')
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError('密码需要 8–128 个字符。')
        return username.lower(), password

    def hash_password(self, password, salt):
        # Bound memory and CPU when several people log in simultaneously.
        if not self.hash_slot.acquire(timeout=2):
            raise RuntimeError('登录繁忙，请稍后再试。')
        try:
            return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000, dklen=32)
        finally:
            self.hash_slot.release()

    @staticmethod
    def display_name(value):
        if not isinstance(value, str):
            raise ValueError('请输入昵称。')
        value = unicodedata.normalize('NFC', value).strip()
        if not 1 <= len(value) <= 24 or any(unicodedata.category(char).startswith('C') for char in value):
            raise ValueError('昵称需要 1–24 个字，不能包含控制字符。')
        return value

    @staticmethod
    def issue(db, username):
        now = int(time.time())
        db.execute('DELETE FROM sessions WHERE expires <= ?', (now,))
        # At most 20 signed-in devices per account; newest sessions survive.
        db.execute('''DELETE FROM sessions WHERE username=? AND token NOT IN
          (SELECT token FROM sessions WHERE username=? ORDER BY expires DESC LIMIT 19)''', (username, username))
        token = secrets.token_hex(32)
        db.execute('INSERT INTO sessions VALUES (?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), username, now + 90 * 86400))
        return token

    def register(self, username, password, allocate, claim=None, level='all', display_name=None):
        username, password = self.credentials(username, password)
        # Older clients can still register without the new field.
        name = '' if display_name is None else self.display_name(display_name)
        salt = secrets.token_bytes(16)
        hashed = self.hash_password(password, salt)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                raise ValueError('这个用户名已使用，请换一个或直接登录。')
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] >= 1000:
                raise RuntimeError('账号空间暂时已满。')
            if claim and db.execute('SELECT 1 FROM users WHERE directory=?', (claim.name,)).fetchone():
                raise ValueError('阅读记录已属于一个账号，请刷新后重试。')
            directory = claim if claim else allocate()
            db.execute('INSERT INTO users (username,directory,salt,password,level,display_name) VALUES (?,?,?,?,?,?)',
                       (username, directory.name, salt, hashed, level, name))
            token = self.issue(db, username)
        return directory, token

    def login(self, username, password):
        username, password = self.credentials(username, password)
        with self.connection() as db:
            row = db.execute('SELECT directory,salt,password FROM users WHERE username=?', (username,)).fetchone()
        actual = self.hash_password(password, row[1] if row else bytes(16))
        if not row or not hmac.compare_digest(actual, row[2]):
            raise ValueError('用户名或密码不正确。')
        with self.connection() as db:
            token = self.issue(db, username)
        return row[0], token

    def resolve(self, token):
        if not re.fullmatch(r'[0-9a-f]{64}', token):
            return None
        with self.connection() as db:
            row = db.execute('''SELECT u.directory FROM sessions s JOIN users u ON u.username=s.username
              WHERE s.token=? AND s.expires>?''',
                             (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        return row[0] if row else None

    def remembered(self, tokens):
        """Resolve only sessions the browser already possesses; never usernames alone."""
        tokens = list(dict.fromkeys(t for t in tokens if re.fullmatch(r'[0-9a-f]{64}', t)))[:9]
        if not tokens:
            return []
        hashes = {hashlib.sha256(t.encode()).hexdigest(): t for t in tokens}
        with self.connection() as db:
            rows = db.execute('SELECT s.token,s.username,u.display_name FROM sessions s JOIN users u ON u.username=s.username WHERE s.expires>? AND s.token IN ('
                              + ','.join('?' for _ in hashes) + ')', (int(time.time()), *hashes)).fetchall()
        found = {row[0]: row[1:] for row in rows}
        result, seen = [], set()
        for digest, token in hashes.items():
            username, name = found.get(digest, (None, None))
            if username and username not in seen:
                result.append({'username': username, 'display_name': name, 'token': token})
                seen.add(username)
        return result

    def logout(self, token):
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def info(self, directory):
        with self.connection() as db:
            row = db.execute('SELECT username,level,display_name FROM users WHERE directory=?', (directory.name,)).fetchone()
        return {'username': row[0], 'level': row[1], 'display_name': row[2]} if row else None

    def rename(self, directory, value):
        name = self.display_name(value)
        with self.connection() as db:
            db.execute('UPDATE users SET display_name=? WHERE directory=?', (name, directory.name))

    def level(self, directory, level):
        with self.connection() as db:
            db.execute('UPDATE users SET level=? WHERE directory=?', (level, directory.name))
