"""Real local/cloud HTTP handlers; replace only TLS transport with a loopback fixture."""
import http.client
import json
from pathlib import Path
import socket
import threading
import time
from unittest.mock import patch
from tests.test_accounts import AccountHTTPTests, Client
from engine.server import ReaderHTTPServer, serve
from engine.local_sync import LocalSync
from tests.test_recordings import sample
from tests.test_assessment_remote import score, TEXT
from engine.recordings import Recordings
from engine.recording_sync import pack


class LocalClient(Client):
    def request(self, path, body=None, headers=None):
        port = self.owner.server.server_port
        return super().request(path, body, {'Host': f'127.0.0.1:{port}',
            'Origin': f'http://127.0.0.1:{port}', **(headers or {})})


class LocalSyncHTTPTests(AccountHTTPTests):
    # Do not rerun inherited cloud-only tests against the local client.
    def setUp(self):
        super().setUp()
        # This transport/isolation fixture has no PDF. OCR and HTTP reference
        # validation have dedicated tests against real pages and corrupt input.
        reference = patch('engine.server.ReadingText.reference',return_value={'text':TEXT})
        reference.start();self.addCleanup(reference.stop)
        self.cloud_server, self.cloud_thread = self.server, self.thread
        self.cloud_client = self.a
        # Keep the cloud client's owner stable while the local handler is tested.
        class Owner:
            server = self.cloud_server
            assertEqual = self.assertEqual
        self.cloud_client.owner = Owner()
        self.assertEqual(self.cloud_client.auth('register')[0], 200)
        self.cloud_client.library()
        cloud_port = self.cloud_server.server_port
        self.outbound = []
        outbound = self.outbound
        class Transport(http.client.HTTPConnection):
            def __init__(self, host, port, timeout):
                super().__init__('127.0.0.1', cloud_port, timeout=timeout)
            def request(self, method, url, body=None, headers=None, **kwargs):
                outbound.append((method, url))
                return super().request(method, url, body, {**headers, 'Host': 'raz.example.test'}, **kwargs)
        self.transport = patch('engine.local_sync.http.client.HTTPSConnection', Transport)
        self.transport.start()
        self.local_root = self.root / 'mac'
        self.local_root.mkdir()
        (self.local_root / 'manifest.json').write_bytes((self.root / 'manifest.json').read_bytes())
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        started = threading.Event()
        owner = self
        class Server(ReaderHTTPServer):
            def __init__(self, address, handler):
                super().__init__(address, handler); owner.server = self; started.set()
        self.factory = patch('engine.server.ReaderHTTPServer', Server)
        self.factory.start()
        self.thread = threading.Thread(target=serve, args=(self.local_root, port),
            kwargs={'sync_origin': 'https://raz.example.test'}, daemon=True)
        self.thread.start(); self.assertTrue(started.wait(5))
        self.a = LocalClient(self)
        self.a.library()
        self.assertEqual(self.a.auth('login')[0], 200)
        self.a.library()

    def tearDown(self):
        self.server.shutdown(); self.thread.join(5)
        self.factory.stop(); self.transport.stop()
        self.server, self.thread = self.cloud_server, self.cloud_thread
        super().tearDown()

    def wait_for(self, condition):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.1)
        self.fail('Background recording sync did not converge')

    def test_progress_audio_and_score_roundtrip_without_cloud_inference(self):
        from datetime import date
        day = date.today().isoformat()
        scope = self.a.scope
        self.assertEqual(scope, self.cloud_client.scope)
        payload = dict(edition_id='edition', page=3, completed=True, completion_days=[day, day],
            reader_scope=scope, writer_id='local-test-writer', sequence=1)
        self.assertEqual(self.a.request('/api/progress', payload)[0], 200)
        # The model runs on Mac. Uploading the result must not invoke cloud scoring.
        version = self.a.request('/api/recordings/edition/2/take', b'')[1]['version']
        self.assertEqual(self.a.request('/api/recordings/edition/2', sample(),
            {'Content-Type': 'audio/wav', 'X-Recording-Version': str(version)})[0], 200)
        with patch('engine.assessment.Assessment.local_run', return_value=score()) as model:
            self.assertEqual(self.a.request('/api/assessment', dict(edition_id='edition',page=2,version=version,text=TEXT))[0], 200)
            self.wait_for(lambda: self.cloud_client.request('/api/recordings/edition')[1].get('pages',{}).get('2',{}).get('assessment'))
            self.assertEqual(model.call_count, 1)
        book = self.wait_for(lambda: (b if (b := self.cloud_client.library()['books'][0]).get('completion_days') == [day] else None))
        self.assertEqual(book['completion_days'], [day])
        self.assertEqual(book['progress']['page'], 3)
        remote = self.cloud_client.request('/api/recordings/edition')[1]['pages']['2']
        self.assertEqual(remote['assessment']['score'], 88)
        self.assertEqual(self.cloud_client.request('/api/recordings/edition/2')[1], sample())
        self.assertTrue(self.a.request('/api/recordings/edition')[1]['pages'])
        self.assertFalse(any(path == '/api/assessment' for _,path in self.outbound))
        self.assertTrue(any(path == '/api/recording-sync' for _,path in self.outbound))
        # A fresh phone recording is pulled when opening that book on Mac.
        phone_version = self.cloud_client.request('/api/recordings/edition/2/take', b'')[1]['version']
        self.assertEqual(self.cloud_client.request('/api/recordings/edition/2', sample(),
            {'Content-Type':'audio/wav','X-Recording-Version':str(phone_version)})[0],200)
        with patch('engine.assessment.Assessment.local_run', return_value={**score(), 'score': 92}):
            self.assertEqual(self.cloud_client.request('/api/assessment', dict(edition_id='edition',page=2,version=phone_version,text=TEXT))[0],200)
        pulled = self.a.request('/api/recordings/edition')[1]['pages']['2']
        self.assertEqual(pulled['assessment']['score'],92)
        self.assertEqual(pulled['assessment']['version'],pulled['version'])
        self.assertNotEqual(pulled['take_id'],remote['take_id'])
        self.assertEqual(self.a.request('/api/recordings/edition/2')[1],sample())
        # Phone continues a different page. Local reload pulls that position.
        self.assertEqual(self.cloud_client.request('/api/progress', dict(edition_id='edition',page=2,completed=True,reader_scope=scope))[0], 200)
        resumed = self.a.library()
        self.assertEqual(resumed['mode'], 'local_sync')
        self.assertEqual(resumed['books'][0]['progress']['page'], 2)
        self.assertEqual(resumed['books'][0]['completion_days'], [day])
        self.assertEqual(resumed['sync_pending'], 0)
        for path in (self.local_root/'local-sync/sessions').glob('*.json'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_recording_outbox_survives_offline_account_switch_and_repeated_delivery(self):
        from engine.local_sync import SyncUnavailable
        original = LocalSync.request
        def offline(bridge, route, *args, **kwargs):
            if route == '/api/recording-sync':
                raise SyncUnavailable('offline fixture')
            return original(bridge, route, *args, **kwargs)
        scope = self.a.scope
        with patch.object(LocalSync, 'request', offline):
            version = self.a.request('/api/recordings/edition/1/take', b'')[1]['version']
            self.assertEqual(self.a.request('/api/recordings/edition/1', sample(),
                {'Content-Type':'audio/wav','X-Recording-Version':str(version)})[0],200)
            self.assertGreater(self.a.library()['sync_pending'],0)
            store = Recordings(self.local_root/'local-sync/readers'/scope)
            envelope = pack(*store.sync_export('edition',1))
            self.assertEqual(self.a.auth('register', username='second_child')[0],200)
            self.a.library()
            self.assertEqual(self.a.request('/api/recordings/edition')[1],{'pages':{}})
        saved = self.wait_for(lambda:self.cloud_client.request('/api/recordings/edition')[1]['pages'].get('1'))
        self.assertEqual(self.a.request('/api/recordings/edition')[1],{'pages':{}})
        # Same snapshot delivered twice cannot allocate a new take/version.
        self.assertEqual(self.cloud_client.request('/api/recording-sync',envelope)[0],200)
        self.assertEqual(self.cloud_client.request('/api/recordings/edition')[1]['pages']['1']['version'],saved['version'])
        # A late old Mac upload cannot overwrite a newer phone recording.
        newer=self.cloud_client.request('/api/recordings/edition/1/take',b'')[1]['version']
        self.cloud_client.request('/api/recordings/edition/1',sample(),{'Content-Type':'audio/wav','X-Recording-Version':str(newer)})
        self.assertTrue(self.cloud_client.request('/api/recording-sync',envelope)[1]['stale'])
        self.assertEqual(self.cloud_client.request('/api/recordings/edition')[1]['pages']['1']['version'],newer)
        self.assertEqual(self.cloud_client.request('/api/recording-sync',envelope,{'X-Reader-Scope':self.a.scope})[0],403)

    def test_guest_recordings_are_not_uploaded_and_existing_user_takes_backfill(self):
        self.assertEqual(self.a.auth('logout')[0],200)
        self.a.library()
        v=self.a.request('/api/recordings/edition/1/take',b'')[1]['version']
        self.a.request('/api/recordings/edition/1',sample(),{'Content-Type':'audio/wav','X-Recording-Version':str(v)})
        self.assertEqual(self.a.library()['sync_pending'],0)
        self.assertEqual(self.a.auth('login')[0],200)
        self.a.library()
        self.assertEqual(self.a.request('/api/recordings/edition')[1],{'pages':{}})
        # Prior local-only sessions had no outbox. Refresh queues them once.
        store=Recordings(self.local_root/'local-sync/readers'/self.a.scope)
        v=store.reserve('edition',2);store.save('edition',2,v,'audio/wav',sample())
        store.save_assessment('edition',2,v,{**score(),'version':v})
        self.a.library()
        result=self.wait_for(lambda:self.cloud_client.request('/api/recordings/edition')[1]['pages'].get('2'))
        self.wait_for(lambda:self.a.library()['sync_pending']==0)
        self.assertEqual(result['assessment']['score'],88)

    def test_offline_outbox_survives_switch_and_rejects_stale_scope(self):
        original_scope = self.a.scope
        original_request = LocalSync.request
        from engine.local_sync import SyncUnavailable
        def offline(bridge, route, *args, **kwargs):
            if route in ('/api/progress', '/api/library'): raise SyncUnavailable('offline fixture')
            return original_request(bridge, route, *args, **kwargs)
        with patch.object(LocalSync, 'request', offline):
            self.assertEqual(self.a.request('/api/progress', dict(edition_id='edition',page=2,completed=False,
                reader_scope=original_scope,writer_id='offline-writer',sequence=1))[0], 200)
            cached = self.a.library()
            self.assertFalse(cached['sync_online'])
            self.assertEqual(cached['books'][0]['progress']['page'], 2)
            self.assertEqual(cached['sync_pending'], 1)
        self.assertEqual(self.a.auth('register', username='second_child')[0], 200)
        new = self.a.library()
        self.assertNotEqual(new['reader_scope'], original_scope)
        self.assertFalse(new['books'][0]['progress'].get('visited'))
        self.assertEqual(self.a.request('/api/progress', dict(edition_id='edition',page=3,reader_scope=original_scope))[0], 409)
        self.assertEqual(self.a.request('/api/recordings/edition', headers={'X-Reader-Scope': original_scope})[0], 403)
        self.assertEqual(self.a.request('/api/account/profile', {'display_name':'x'}, {'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.a.auth('switch')[0], 200)
        self.assertEqual(self.a.library()['books'][0]['progress']['page'], 2)


# unittest discovers inherited methods; these cloud cases already run in their
# own module. Keep this integration class focused on the new bridge contract.
for name in dir(AccountHTTPTests):
    if name.startswith('test_') and name not in LocalSyncHTTPTests.__dict__:
        setattr(LocalSyncHTTPTests, name, None)
