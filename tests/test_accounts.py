"""Real HTTP account flow, isolation, and compatibility with anonymous readers."""
import http.client
import hashlib
from http.cookies import SimpleCookie
import json
from pathlib import Path
import tempfile
import sqlite3
import threading
import unittest
from unittest.mock import patch

from engine.accounts import Accounts
from engine.cloud_sessions import CloudSessions
from engine.recordings import Recordings
from engine.server import ReaderHTTPServer, serve
from tests.test_recordings import sample


class Client:
    def __init__(self, owner):
        self.owner, self.cookie, self.scope = owner, '', None

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.owner.server.server_port, timeout=5)
        h = {'Host': 'raz.example.test', 'Origin': 'https://raz.example.test', 'Cookie': self.cookie}
        if self.scope:
            h['X-Reader-Scope'] = self.scope
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode()
            h['Content-Type'] = 'application/json'
        connection.request('GET' if body is None else 'POST', path, body, {**h, **(headers or {})})
        response = connection.getresponse()
        jar = SimpleCookie(self.cookie)
        for name, value in response.getheaders():
            if name.lower() == 'set-cookie':
                for key, morsel in SimpleCookie(value).items():
                    if morsel['max-age'] == '0':
                        jar.pop(key, None)
                    else:
                        jar[key] = morsel.value
        self.cookie = '; '.join(f'{key}={morsel.value}' for key, morsel in jar.items())
        raw = response.read()
        result = json.loads(raw) if response.getheader('Content-Type').startswith('application/json') else raw
        status = response.status
        connection.close()
        return status, result

    def library(self):
        # The library is the identity discovery endpoint, so don't bind its scope.
        self.scope = None
        status, result = self.request('/api/library')
        self.owner.assertEqual(status, 200)
        self.scope = result['reader_scope']
        return result

    def auth(self, action, username='reader_one', **body):
        return self.request('/api/account/' + action, {'username': username, 'password': 'test-only-passphrase', **body})


class AccountHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        book = dict(book_id='book', edition_id='edition', page_count=3, level='AA', title='Test book',
                    title_status='verified', demo=False, validation='ok', authorization={}, reader_source='pdf',
                    audio={'page_map': {}, 'status': 'missing'})
        book['cloud_public'] = dict(book)
        (self.root / 'manifest.json').write_text(json.dumps({'books': [book], 'levels': [{'level': 'AA', 'exists': True}], 'errors': []}))
        started = threading.Event()
        owner = self

        class Server(ReaderHTTPServer):
            def __init__(self, address, handler):
                super().__init__(address, handler)
                owner.server = self
                started.set()

        self.patches = [patch('engine.server.ReaderHTTPServer', Server), patch('engine.server.Assessment.warmup')]
        for item in self.patches:
            item.start()
        self.thread = threading.Thread(target=serve, args=(self.root, 0), kwargs={'cloud_origin': 'https://raz.example.test'}, daemon=True)
        self.thread.start()
        self.assertTrue(started.wait(5))
        self.a = Client(self)
        self.a.library()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_claim_guest_and_reconnect_from_second_device_with_progress_audio_scores_and_level(self):
        guest_cookie, guest_scope = self.a.cookie, self.a.scope
        self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 2, 'completed': False})[0], 200)
        sessions = CloudSessions(self.root, 'https://raz.example.test')
        directory, _ = sessions.resolve(guest_cookie)
        recordings = Recordings(directory)
        version = recordings.reserve('edition', 2)
        recordings.save('edition', 2, version, 'audio/wav', sample())
        recordings.save_assessment('edition', 2, version, {'score': 91, 'version': version})
        self.assertEqual(self.a.auth('register', import_progress=True, level='AA')[0], 200)
        library = self.a.library()
        self.assertNotEqual(guest_scope, self.a.scope)
        self.assertEqual(library['account'], {'username': 'reader_one', 'level': 'AA', 'display_name': ''})
        self.assertEqual(sessions.resolve(guest_cookie), (None, None))
        other = Client(self)
        other.library()
        self.assertEqual(other.auth('login', username='READER_ONE')[0], 200)
        same = other.library()
        self.assertEqual(other.scope, self.a.scope)
        self.assertEqual(same['books'][0]['progress']['page'], 2)
        self.assertEqual(same['books'][0]['assessment_summary']['average'], 91)
        self.assertEqual(other.request('/api/recordings/edition/2')[1], sample())
        self.assertEqual(other.request('/api/account/preferences', {'level': 'all'})[0], 200)
        self.assertEqual(self.a.library()['account']['level'], 'all')
        self.assertEqual((self.root / 'accounts.sqlite3').stat().st_mode & 0o777, 0o600)
        self.assertNotIn(b'test-only-passphrase', (self.root / 'accounts.sqlite3').read_bytes())

    def test_accounts_isolated_and_stale_tabs_cannot_write_or_play_under_a_new_cookie(self):
        guest_scope = self.a.scope
        self.assertEqual(self.a.auth('register')[0], 200)
        self.a.library()
        first_cookie, first_scope = self.a.cookie, self.a.scope
        self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 3, 'completed': True, 'reader_scope': first_scope})[0], 200)
        self.assertEqual(self.a.auth('logout')[0], 200)
        self.a.library()
        self.assertEqual(self.a.auth('register', username='reader_two')[0], 200)
        new_library = self.a.library()
        self.assertEqual(new_library['books'][0]['progress'], {'page': 1, 'completed': False})
        self.assertEqual(self.a.request('/api/recordings/edition')[1], {'pages': {}})
        for old_scope in [guest_scope, first_scope]:
            self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 2, 'reader_scope': old_scope})[0], 409)
            self.assertEqual(self.a.request('/api/recordings/edition/1/take', b'', {'X-Reader-Scope': old_scope})[0], 403)
            self.assertEqual(self.a.request('/api/recordings/edition/1?scope=' + old_scope)[0], 403)
        self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 2})[0], 409)
        revoked = Client(self)
        revoked.cookie = first_cookie
        self.assertEqual(revoked.request('/api/recordings/edition')[0], 403)
        self.assertEqual(self.a.library()['books'][0]['progress'], {'page': 1, 'completed': False})

    def test_credentials_origin_and_session_tampering_are_rejected(self):
        for username, password in [('x', '12345678'), ('../reader', '12345678'), ('valid', 'short')]:
            self.assertEqual(self.a.auth('register', username=username, password=password)[0], 400)
        self.assertEqual(self.a.auth('register')[0], 200)
        self.a.library()
        other = Client(self)
        other.library()
        self.assertEqual(other.auth('register')[0], 400)
        self.assertEqual(other.auth('login', password='incorrect-password')[0], 400)
        self.assertEqual(other.request('/api/account/login', {'username': 'reader_one', 'password': 'test-only-passphrase'}, {'Origin': 'https://foreign.test'})[0], 403)
        cookie = SimpleCookie(self.a.cookie)['__Host-raz_session'].value
        other.cookie = '__Host-raz_session=' + cookie[:-1] + ('0' if cookie[-1] != '0' else '1')
        self.assertEqual(other.request('/api/recordings/edition')[0], 403)
        store = Accounts(self.root)
        with store.connection() as db:
            db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.a.request('/api/recordings/edition')[0], 403)

    def test_multiple_children_switch_only_after_login_and_keep_progress_audio_and_levels_separate(self):
        self.assertEqual(self.a.auth('register')[0], 200)
        one = self.a.library()
        one_scope = self.a.scope
        self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 2,
                                                        'reader_scope': one_scope})[0], 200)
        self.a.request('/api/account/preferences', {'level': 'AA'})
        _, take = self.a.request('/api/recordings/edition/1/take', b'')
        self.assertEqual(self.a.request('/api/recordings/edition/1', sample(),
          {'Content-Type': 'audio/wav', 'X-Recording-Version': str(take['version'])})[0], 200)
        self.assertEqual(self.a.auth('register', username='reader_two', import_progress=True)[0], 400)
        self.assertEqual(self.a.auth('register', username='reader_two')[0], 200)
        two = self.a.library()
        self.assertEqual({a['username'] for a in two['saved_accounts']}, {'reader_one', 'reader_two'})
        self.assertTrue(next(a for a in two['saved_accounts'] if a['username'] == 'reader_two')['active'])
        self.assertNotIn('token', json.dumps(two['saved_accounts']))
        self.assertFalse(two['books'][0]['progress'].get('visited'))
        self.assertEqual(self.a.request('/api/recordings/edition')[1], {'pages': {}})
        self.assertEqual(self.a.request('/api/progress', {'edition_id': 'edition', 'page': 3,
                                                        'reader_scope': one_scope})[0], 409)
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_one'})[0], 200)
        resumed = self.a.library()
        self.assertEqual(resumed['account']['level'], 'AA')
        self.assertEqual(resumed['books'][0]['progress']['page'], 2)
        self.assertEqual(self.a.request('/api/recordings/edition/1')[1], sample())
        stranger = Client(self)
        stranger.library()
        self.assertEqual(stranger.request('/api/account/switch', {'username': 'reader_one'})[0], 400)
        stranger.auth('register', username='reader_three')
        stranger.library()
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_three'})[0], 400)

    def test_removing_a_saved_account_revokes_only_this_device_and_logout_keeps_other_children(self):
        self.a.auth('register')
        self.a.library()
        other_device = Client(self)
        other_device.library()
        other_device.auth('login')
        other_device.library()
        self.a.auth('register', username='reader_two')
        self.a.library()
        self.assertEqual(self.a.request('/api/account/forget', {'username': 'reader_one'})[0], 200)
        self.a.library()
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_one'})[0], 400)
        self.assertEqual(other_device.library()['account']['username'], 'reader_one')
        self.a.auth('login')
        self.a.library()
        self.assertEqual(self.a.auth('logout')[0], 200)
        guest = self.a.library()
        self.assertIsNone(guest['account'])
        self.assertEqual(guest['saved_accounts'], [{'username': 'reader_two', 'display_name': '', 'active': False}])
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_two'})[0], 200)
        self.assertEqual(self.a.library()['account']['username'], 'reader_two')

    def test_expired_and_forged_remembered_sessions_cannot_switch(self):
        self.a.auth('register')
        self.a.library()
        self.a.auth('register', username='reader_two')
        self.a.library()
        store = Accounts(self.root)
        with store.connection() as db:
            db.execute('UPDATE sessions SET expires=0 WHERE username=?', ('reader_one',))
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_one'})[0], 400)
        jar = SimpleCookie(self.a.cookie)
        jar['__Host-raz_accounts'] = 'v1-' + '0' * 64
        self.a.cookie = '; '.join(f'{key}={morsel.value}' for key, morsel in jar.items())
        self.assertEqual(self.a.library()['saved_accounts'], [{'username': 'reader_two', 'display_name': '', 'active': True}])

    def test_nickname_updates_are_scoped_and_survive_switching_and_new_devices(self):
        self.assertEqual(self.a.request('/api/account/profile', {'display_name': '小鹿'})[0], 400)
        self.assertEqual(self.a.auth('register', display_name='  小鹿  ')[0], 200)
        one = self.a.library()
        self.assertEqual(one['account']['display_name'], '小鹿')
        first_scope = self.a.scope
        self.a.request('/api/progress', {'edition_id': 'edition', 'page': 2, 'reader_scope': first_scope})
        self.assertEqual(self.a.auth('register', username='reader_two', display_name='小鹿')[0], 200)
        two = self.a.library()
        self.assertEqual(len(two['saved_accounts']), 2)  # Same nickname does not merge identities.
        self.assertEqual(self.a.request('/api/account/profile', {'display_name': '越权'}, {'X-Reader-Scope': first_scope})[0], 403)
        self.assertEqual(self.a.request('/api/account/profile', {'display_name': '小兔', 'username': 'reader_one'})[0], 200)
        for bad in ['', '  ', '字' * 25, 'a\nname', '\u202ename', 42, None]:
            self.assertEqual(self.a.request('/api/account/profile', {'display_name': bad})[0], 400)
        self.assertEqual(self.a.library()['account']['display_name'], '小兔')
        self.assertEqual(self.a.request('/api/account/switch', {'username': 'reader_one'})[0], 200)
        resumed = self.a.library()
        self.assertEqual(resumed['account']['display_name'], '小鹿')
        self.assertEqual(resumed['books'][0]['progress']['page'], 2)
        other = Client(self); other.library()
        self.assertEqual(other.auth('login', username='reader_two')[0], 200)
        self.assertEqual(other.library()['account']['display_name'], '小兔')


class AccountMigrationTests(unittest.TestCase):
    def test_existing_accounts_keep_login_directory_and_level_when_nickname_is_added(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'legacy-directory'
            directory.mkdir()
            salt = bytes(range(16))
            hashed = hashlib.pbkdf2_hmac('sha256', b'legacy-password', salt, 600000, dklen=32)
            with sqlite3.connect(root / 'accounts.sqlite3') as db:
                db.execute('CREATE TABLE users (username TEXT PRIMARY KEY,directory TEXT NOT NULL UNIQUE,salt BLOB NOT NULL,password BLOB NOT NULL,level TEXT NOT NULL)')
                db.execute('INSERT INTO users VALUES (?,?,?,?,?)', ('legacy_reader', directory.name, salt, hashed, 'G'))
            accounts = Accounts(root)
            self.assertEqual(accounts.info(directory), {'username': 'legacy_reader', 'level': 'G', 'display_name': ''})
            location, token = accounts.login('legacy_reader', 'legacy-password')
            self.assertEqual(location, directory.name)
            accounts.rename(directory, '小鹿')
            again = Accounts(root)  # Migration is idempotent and keeps existing sessions.
            self.assertEqual(again.resolve(token), directory.name)
            self.assertEqual(again.info(directory)['display_name'], '小鹿')
