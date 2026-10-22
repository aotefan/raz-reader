"""Guest and account sessions. All private state lives outside the public site.

Only the trusted reverse proxy reaches this service. It overwrites X-Real-IP.
Guest cookies are signed; account cookies carry random session tokens.
Both are Secure and HttpOnly; saved accounts require a valid session on this device.
"""
from collections import OrderedDict
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
import secrets
import threading
import time

from engine.recordings import Recordings
from engine.accounts import Accounts

COOKIE = '__Host-raz_session'
ACCOUNTS_COOKIE = '__Host-raz_accounts'


class CloudSessions:
    def __init__(self, directory, origin):
        self.directory = Path(directory) / 'visitors'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.origin = origin.rstrip('/')
        self.host = self.origin.removeprefix('https://')
        if not self.origin.startswith('https://') or '/' in self.host:
            raise ValueError('Cloud origin must be an HTTPS origin')
        key = Path(directory) / 'session.key'
        if not key.exists():
            with key.open('xb') as output:
                key.chmod(0o600)
                output.write(secrets.token_bytes(32))
        self.key = key.read_bytes()
        self.lock = threading.Lock()
        self.buckets = OrderedDict()
        self.count = sum(p.is_dir() for p in self.directory.iterdir())
        self.accounts = Accounts(directory)

    def rate(self, identity, category, limit, window):
        now = time.monotonic()
        key = (identity, category)
        with self.lock:
            started, count = self.buckets.get(key, (now, 0))
            if now - started >= window:
                started, count = now, 0
            if count >= limit:
                return False
            self.buckets[key] = (started, count + 1)
            self.buckets.move_to_end(key)
            while len(self.buckets) > 10000:
                self.buckets.popitem(last=False)
        return True

    def signature(self, identifier):
        return hmac.new(self.key, identifier.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def token(raw_cookie, name=COOKIE):
        cookie = SimpleCookie()
        try:
            cookie.load(raw_cookie or '')
            token = cookie[name].value if name in cookie else ''
        except CookieError:
            token = ''
        return token

    @staticmethod
    def account_cookie(token):
        return f'{COOKIE}=a-{token}; Path=/; Max-Age=7776000; HttpOnly; Secure; SameSite=Strict'

    def remembered(self, raw_cookie):
        saved = self.token(raw_cookie, ACCOUNTS_COOKIE)
        tokens = saved[3:].split('.')[:8] if saved.startswith('v1-') and len(saved) <= 530 else []
        current = self.token(raw_cookie)
        # Old one-account sessions migrate automatically when adding an account.
        if current.startswith('a-'):
            tokens = [current[2:], *tokens]
        return self.accounts.remembered(tokens)

    @staticmethod
    def remembered_cookie(entries):
        value = 'v1-' + '.'.join(item['token'] for item in entries[:8]) if entries else ''
        age = 7776000 if entries else 0
        return f'{ACCOUNTS_COOKIE}={value}; Path=/; Max-Age={age}; HttpOnly; Secure; SameSite=Strict'

    def scope(self, directory):
        kind = 'u' if self.accounts.info(directory) else 'g'
        return kind + '-' + self.signature('scope:' + kind + ':' + directory.name)

    def resolve(self, raw_cookie, create=False):
        token = self.token(raw_cookie)
        if token.startswith('a-'):
            account_directory = self.accounts.resolve(token[2:])
            if account_directory and (self.directory / account_directory).is_dir():
                return self.directory / account_directory, None
        identifier, _, signature = token.partition('.')
        valid = (len(identifier) == 64 and all(c in '0123456789abcdef' for c in identifier)
                 and hmac.compare_digest(signature, self.signature(identifier)))
        if valid and (self.directory / identifier).is_dir() and not self.accounts.info(self.directory / identifier):
            return self.directory / identifier, None
        if not create:
            return None, None
        with self.lock:
            if self.count >= 1000 or self.disk_usage() >= 2 * 1024**3:
                raise RuntimeError('阅读空间暂时已满，请稍后重试。')
            identifier = secrets.token_hex(32)
            directory = self.directory / identifier
            directory.mkdir(mode=0o700)
            self.count += 1
        token = identifier + '.' + self.signature(identifier)
        return directory, f'{COOKIE}={token}; Path=/; Max-Age=31536000; HttpOnly; Secure; SameSite=Strict'

    def disk_usage(self):
        return sum(p.stat().st_size for p in self.directory.glob('*/*') if p.is_file())

    def save(self, recordings, edition, page, version, mime, audio):
        # Serialize quota checks with saves, including writes by other visitors.
        with self.lock:
            self.check_recording_quota(recordings, edition, page, len(audio))
            return recordings.save(edition, page, version, mime, audio)

    def check_recording_quota(self, recordings, edition, page, size):
        with recordings.connection() as db:
            used = db.execute('SELECT COALESCE(SUM(length(audio)),0) FROM recordings').fetchone()[0]
            previous = db.execute('SELECT length(audio) FROM recordings WHERE edition=? AND page=?',
                                  (edition, page)).fetchone()
        if used - ((previous[0] or 0) if previous else 0) + size > 128 * 1024**2:
            raise ValueError('你的录音空间已满，请稍后再试。')
        if self.disk_usage() + size > 2 * 1024**3:
            raise ValueError('录音空间暂时已满，请稍后再试。')
