"""Loopback account/progress bridge with asynchronous private recording sync.

The browser talks only to localhost. Cloud cookies stay in owner-only files;
only an allowlisted HTTPS origin and bounded private endpoints are used.
Progress and its durable outbox commit together. Each queued write keeps the
original account credential/scope, so switching children cannot mix records.
"""
import copy
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import uuid
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit
from engine.pipeline import connect
from engine.recordings import Recordings
from engine.recording_sync import MAX_MESSAGE, pack, unpack, stamp

COOKIE = 'raz_local_session'
CLOUD_COOKIES = {'__Host-raz_session', '__Host-raz_accounts'}
ACTIONS = {'login', 'register', 'logout', 'switch', 'forget', 'profile', 'preferences'}


class SyncUnavailable(RuntimeError):
    pass


class LocalSync:
    def __init__(self, root, origin):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Sync origin must be a plain HTTPS origin')
        self.origin = origin.rstrip('/')
        self.host, self.port = parsed.hostname, parsed.port or 443
        self.root = Path(root) / 'local-sync'
        self.sessions = self.root / 'sessions'
        self.readers = self.root / 'readers'
        for directory in (self.root, self.sessions, self.readers):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self._run, name='reader-sync', daemon=True)
        self.worker.start()

    def request(self, route, cookies, scope=None, body=None):
        recording_route = re.fullmatch(r'/api/(?:recordings/[A-Za-z0-9_-]+|recording-sync(?:/[A-Za-z0-9_-]+/[1-9][0-9]*)?)', route)
        if route not in ('/api/library', '/api/progress') and route not in {'/api/account/' + a for a in ACTIONS} and not recording_route:
            raise ValueError('Endpoint is not allowed for reader synchronization')
        headers = {'Origin': self.origin, 'Cookie': cookies, 'Accept': 'application/json'}
        if scope:
            headers['X-Reader-Scope'] = scope
        binary = isinstance(body, bytes)
        payload = body if binary else None if body is None else json.dumps(body).encode()
        if payload is not None:
            headers['Content-Type'] = 'application/vnd.raz.recording' if binary else 'application/json'
        connection = http.client.HTTPSConnection(self.host, self.port, timeout=15 if recording_route else 3)
        try:
            connection.request('GET' if body is None else 'POST', route, payload, headers)
            response = connection.getresponse()
            # Deliberately no redirects: account credentials never follow a URL.
            limit = MAX_MESSAGE if recording_route else 4 * 1024 * 1024
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise ValueError('Oversized sync response')
            if response.getheader('Content-Type', '').startswith('application/vnd.raz.recording'):
                return response.status, unpack(raw), []
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('Invalid sync response')
            return response.status, result, [v for k, v in response.getheaders() if k.lower() == 'set-cookie']
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise SyncUnavailable('暂时连接不到云端。已保存的本机进度仍可使用，联网后自动同步。') from error
        finally:
            connection.close()

    @staticmethod
    def cookie_header(state):
        return '; '.join(f'{k}={v}' for k, v in state.get('cookies', {}).items() if k in CLOUD_COOKIES)

    @staticmethod
    def update_cookies(state, headers):
        for header in headers:
            jar = SimpleCookie()
            try:
                jar.load(header)
            except CookieError:
                continue
            for name, morsel in jar.items():
                if name in CLOUD_COOKIES:
                    if morsel['max-age'] == '0':
                        state.setdefault('cookies', {}).pop(name, None)
                    else:
                        state.setdefault('cookies', {})[name] = morsel.value

    def save(self, identifier, state):
        path = self.sessions / (identifier + '.json')
        temporary = path.with_suffix('.next')
        with temporary.open('w') as output:
            os.chmod(temporary, 0o600)
            json.dump(state, output)
        temporary.replace(path)

    def refresh(self, identifier, state):
        status, result, headers = self.request('/api/library', self.cookie_header(state))
        if status != 200 or not re.fullmatch(r'[ug]-[a-f0-9]{64}', result.get('reader_scope', '')):
            raise SyncUnavailable('云端账号暂不可用，请稍后重试。')
        self.update_cookies(state, headers)
        # Book media stays local. Recording summaries carry no microphone bytes.
        state['library'] = {k: result.get(k) for k in ('reader_scope', 'account', 'saved_accounts')}
        state['library']['books'] = [{k: b.get(k) for k in
            ('edition_id', 'progress', 'completion_days', 'acknowledged_writes', 'assessment_summary')} for b in result['books']]
        state['online'] = True
        state.pop('auth_pending', None)
        self.save(identifier, state)
        if state['library'].get('account'):
            self.recordings(state).backfill_sync()
            self.wake.set()

    def recordings(self, state):
        library = state['library']
        context = {'scope': library['reader_scope'], 'cookies': self.cookie_header(state),
                   'editions': {b['edition_id'] for b in library['books']}} if library.get('account') else None
        return Recordings(self.directory(state), context)

    def recording_pending(self, directory):
        store = Recordings(directory)
        with store.connection() as db:
            return db.execute('SELECT COUNT(*) FROM recording_outbox').fetchone()[0]

    def pull_recordings(self, state, edition):
        """On book open, fetch only missing/changed takes from that same account."""
        if not state.get('online') or not state['library'].get('account') or edition not in {b['edition_id'] for b in state['library']['books']}:
            return
        store = self.recordings(state)
        try:
            status, result, _ = self.request('/api/recordings/' + edition, self.cookie_header(state), state['library']['reader_scope'])
            if status != 200:
                return
            current = store.list(edition)
            with store.connection() as db:
                pending = {str(r[0]) for r in db.execute('SELECT page FROM recording_outbox WHERE edition=?', (edition,))}
            for page, remote in result['pages'].items():
                if page in pending:
                    continue  # Push first; delayed pulls cannot erase unuploaded work.
                local = current.get(page)
                if local and remote.get('take_id') == local.get('take_id') and remote.get('assessment_at') == local.get('assessment_at'):
                    continue
                status, payload, _ = self.request(f'/api/recording-sync/{edition}/{int(page)}',
                    self.cookie_header(state), state['library']['reader_scope'])
                if status == 200:
                    metadata, audio = payload
                    if metadata.get('edition_id') == edition and metadata.get('page') == int(page):
                        store.sync_import(metadata, audio, downloading=True)
        except (SyncUnavailable, ValueError, OSError, sqlite3.Error):
            return  # Already-downloaded recordings remain available offline.

    def resolve(self, raw_cookie, refresh=False):
        jar = SimpleCookie()
        try:
            jar.load(raw_cookie or '')
        except CookieError:
            pass
        identifier = jar[COOKIE].value if COOKIE in jar else ''
        with self.lock:
            path = self.sessions / (identifier + '.json') if re.fullmatch(r'[a-f0-9]{64}', identifier) else None
            cookie = None
            if path is None or not path.is_file():
                if not refresh:
                    return None, None, None
                identifier = secrets.token_hex(32)
                state = {'cookies': {}}
                cookie = f'{COOKIE}={identifier}; Path=/; HttpOnly; SameSite=Strict; Max-Age=7776000'
            else:
                state = json.loads(path.read_text())
            if refresh or not state.get('library') or state.get('auth_pending'):
                try:
                    self.refresh(identifier, state)
                except SyncUnavailable:
                    if not state.get('library') or state.get('auth_pending'):
                        raise
                    state['online'] = False
            return identifier, copy.deepcopy(state), cookie

    def directory(self, state):
        scope = state['library']['reader_scope']
        if not re.fullmatch(r'[ug]-[a-f0-9]{64}', scope):
            raise ValueError('Invalid reader scope')
        directory = self.readers / scope
        directory.mkdir(mode=0o700, exist_ok=True)
        return directory

    def account(self, identifier, scope, action, body):
        if action not in ACTIONS:
            return 404, {'error': 'Unknown endpoint'}
        with self.lock:
            state = json.loads((self.sessions / (identifier + '.json')).read_text())
            if state['library']['reader_scope'] != scope:
                return 409, {'error': '账号已切换，请刷新网页。'}
            guest = self.directory(state) if action == 'register' and body.get('import_progress') is True and not state['library'].get('account') else None
            status, result, headers = self.request('/api/account/' + action, self.cookie_header(state), scope, body)
            if status == 200:
                self.update_cookies(state, headers)
                state['auth_pending'] = True
                self.save(identifier, state)  # Persist login before the next request.
                self.refresh(identifier, state)
                if guest is not None and state['library'].get('account'):
                    self.import_progress(guest, state)
                if state['library'].get('account'):
                    self.resume(state)
            return status, result

    def resume(self, state):
        # A fresh login can resume that same reader's expired queue. The signed
        # cloud scope, never a nickname/username supplied by the client, selects it.
        path = self.directory(state) / 'library.sqlite3'
        if path.exists():
            with sqlite3.connect(path, timeout=2) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_outbox'").fetchone():
                    db.execute('UPDATE sync_outbox SET cookies=?, blocked=0', (self.cookie_header(state),))
            self.wake.set()
        store = self.recordings(state)
        with store.connection() as db:
            db.execute('UPDATE recording_outbox SET cookies=?,blocked=0 WHERE scope=?',
                       (self.cookie_header(state), state['library']['reader_scope']))
        store.backfill_sync()
        self.wake.set()

    def import_progress(self, guest, state):
        # Explicit registration opt-in copies only progress, never guest audio.
        with connect(guest) as source, connect(self.directory(state)) as target:
            for edition, page, completed, updated in source.execute('SELECT * FROM progress'):
                days = [r[0] for r in source.execute('SELECT day FROM completions WHERE edition_id=?', (edition,))]
                target.execute('INSERT OR IGNORE INTO progress VALUES (?, ?, ?, ?)', (edition, page, completed, updated))
                target.executemany('INSERT OR IGNORE INTO completions VALUES (?, ?)', [(edition, day) for day in days])
                self.enqueue(target, {'edition_id': edition, 'page': page, 'completed': bool(completed),
                    'completion_days': days[-32:], 'writer_id': str(uuid.uuid4()), 'sequence': 1,
                    'reader_scope': state['library']['reader_scope']}, state)

    def enqueue(self, db, body, state):
        body = {k: body[k] for k in ('edition_id', 'page', 'completed', 'completion_days',
            'writer_id', 'sequence', 'read_at', 'reader_scope') if k in body}
        library = state['library']
        # Only books that exist in the cloud can be continued on the phone.
        if not library.get('account') or not any(b['edition_id'] == body['edition_id'] for b in library['books']):
            return
        db.execute('''CREATE TABLE IF NOT EXISTS sync_outbox
            (edition_id TEXT PRIMARY KEY, payload TEXT NOT NULL, cookies TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0)''')
        previous = db.execute('SELECT payload FROM sync_outbox WHERE edition_id=?', (body['edition_id'],)).fetchone()
        if previous:
            body = {**body, 'completion_days': sorted(set(body.get('completion_days', []) + json.loads(previous[0]).get('completion_days', [])))[-32:]}
        db.execute('''INSERT INTO sync_outbox VALUES (?, ?, ?, 0) ON CONFLICT(edition_id)
            DO UPDATE SET payload=excluded.payload, cookies=excluded.cookies, blocked=0''',
                   (body['edition_id'], json.dumps(body), self.cookie_header(state)))
        self.wake.set()

    def pending(self, directory):
        path = directory / 'library.sqlite3'
        if not path.exists():
            return set()
        with sqlite3.connect(path, timeout=2) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_outbox'").fetchone():
                return set()
            return {r[0] for r in db.execute('SELECT edition_id FROM sync_outbox')}

    def flush(self):
        for path in self.readers.glob('*/library.sqlite3'):
            try:
                with sqlite3.connect(path, timeout=2) as db:
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_outbox'").fetchone():
                        continue
                    rows = db.execute('SELECT edition_id, payload, cookies FROM sync_outbox WHERE blocked=0').fetchall()
                for edition, serialized, cookies in rows:
                    body = json.loads(serialized)
                    status, result, _ = self.request('/api/progress', cookies, body['reader_scope'], body)
                    with sqlite3.connect(path, timeout=2) as db:
                        if status == 200 and (result.get('saved') or result.get('stale')):
                            db.execute('DELETE FROM sync_outbox WHERE edition_id=? AND payload=?', (edition, serialized))
                        elif status in (400, 401, 403, 404, 409):
                            # Retain locally, but never retry under another account.
                            db.execute('UPDATE sync_outbox SET blocked=1 WHERE edition_id=? AND payload=?', (edition, serialized))
            except (OSError, sqlite3.Error, SyncUnavailable):
                continue

    def _run(self):
        while not self.closed.is_set():
            self.wake.wait(5)
            self.wake.clear()
            if not self.closed.is_set():
                self.flush()
                self.flush_recordings()

    def flush_recordings(self):
        sent = 0
        for path in self.readers.glob('*/recordings.sqlite3'):
            try:
                store = Recordings(path.parent)
                with store.connection() as db:
                    rows = db.execute('SELECT edition,page,stamp,cookies,scope FROM recording_outbox WHERE blocked=0').fetchall()
                for edition, page, queued_stamp, cookies, scope in rows:
                    if self.closed.is_set() or sent >= 8:
                        return
                    exported = store.sync_export(edition, page)
                    if not exported or stamp(exported[0]) != queued_stamp:
                        continue
                    try:
                        payload = pack(*exported)
                    except (ValueError, TypeError):
                        with store.connection() as db:
                            db.execute('UPDATE recording_outbox SET blocked=1 WHERE edition=? AND page=? AND stamp=?',
                                       (edition, page, queued_stamp))
                        continue  # Keep the original; one oversized take cannot block the rest.
                    status, result, _ = self.request('/api/recording-sync', cookies, scope, payload)
                    sent += 1
                    with store.connection() as db:
                        if status == 200 and result.get('accepted'):
                            db.execute('DELETE FROM recording_outbox WHERE edition=? AND page=? AND stamp=?',
                                       (edition, page, queued_stamp))
                            # Only this exact snapshot was acknowledged; a later score
                            # or take still has a different stamp and stays queued.
                            db.execute('UPDATE recordings SET synced_stamp=? WHERE edition=? AND page=? AND take_id=? AND COALESCE(assessment_at,?)=?',
                                (queued_stamp, edition, page, exported[0]['take_id'], '', exported[0].get('assessment_at') or ''))
                        elif status in (400, 401, 403, 404, 409, 413):
                            db.execute('UPDATE recording_outbox SET blocked=1 WHERE edition=? AND page=? AND stamp=?',
                                       (edition, page, queued_stamp))
                    # Existing accounts may have a large first-time backlog.
                    # Keep it below the cloud's per-account request allowance.
                    self.closed.wait(.8)
            except (OSError, ValueError, sqlite3.Error, SyncUnavailable):
                continue

    def close(self):
        self.closed.set()
        self.wake.set()
        self.worker.join(timeout=20)
