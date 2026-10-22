import base64
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from engine.assessment import Assessment, METHOD
from engine.assessment_remote import RemoteAssessment, valid_result
from engine.recordings import Recordings
from engine.server import ReaderHTTPServer, serve
from scripts.mac_assessment_worker import decode_job, WorkerConnection
from tests.test_recordings import sample


TOKEN = 'a' * 64
TEXT = 'A red egg.'


def score():
    return {'score': 88, 'method': METHOD, 'reference_only': True, 'duration': 1.2,
            'reference': TEXT, 'words': [
                {'word': w, 'position': i, 'status': 'matched', 'expected': w,
                 'heard': None, 'phones': []} for i, w in enumerate(('a', 'red', 'egg'))],
            'phone_error_rate': .12, 'weighted_phone_error_rate': .12,
            'heard_phones': ['r', 'ɛ', 'd'], 'offline': True}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.remote = RemoteAssessment(TOKEN, poll_seconds=.5, lease_seconds=.15, task_seconds=.4)
        self.pool = ThreadPoolExecutor(4)
        self.addCleanup(self.pool.shutdown)
        self.addCleanup(self.remote.close)

    def ready(self):
        future = self.pool.submit(self.remote.poll)
        with self.remote.condition:
            self.assertTrue(self.remote.condition.wait_for(lambda: self.remote.waiter is not None, 1))
        return future

    def assign(self):
        poll = self.ready()
        result = self.pool.submit(self.remote.try_run, b'private-audio', TEXT)
        return result, poll.result(1)

    def test_offline_and_busy_fall_back_immediately_without_queueing(self):
        started = time.monotonic()
        self.assertIsNone(self.remote.try_run(b'a', TEXT))
        self.assertLess(time.monotonic() - started, .05)
        result, job = self.assign()
        self.assertIsNone(self.remote.try_run(b'other-account', TEXT))
        self.assertTrue(self.remote.finish(job['id'], score()))
        self.assertEqual(result.result(1), score())
        self.assertIsNone(self.remote.job)

    def test_only_opaque_job_id_audio_and_reference_are_sent(self):
        result, job = self.assign()
        self.assertEqual(set(job), {'id', 'text', 'audio'})
        self.assertEqual(decode_job(job), (b'private-audio', TEXT))
        self.assertFalse(self.remote.finish('b' * 48, score()))
        self.assertTrue(self.remote.finish(job['id'], score()))
        self.assertEqual(result.result(1), score())
        self.assertFalse(self.remote.finish(job['id'], score()))

    def test_lost_connection_and_late_results_fall_back_with_bounded_wait(self):
        result, job = self.assign()
        started = time.monotonic()
        self.assertIsNone(result.result(1))
        self.assertLess(time.monotonic() - started, .3)
        self.assertFalse(self.remote.heartbeat(job['id']))
        self.assertFalse(self.remote.finish(job['id'], score()))
        self.assertEqual(self.remote.status()['counts']['timeout'], 1)
        self.assertIsNone(self.remote.job)

    def test_heartbeat_extends_liveness_but_never_absolute_deadline(self):
        result, job = self.assign()
        for _ in range(3):
            time.sleep(.09)
            self.assertTrue(self.remote.heartbeat(job['id']))
        self.assertIsNone(result.result(1))
        self.assertFalse(self.remote.finish(job['id'], score()))

    def test_bad_or_crashed_worker_falls_back_and_valid_input_error_is_preserved(self):
        for payload in ({'worker_error': True}, {**score(), 'reference': 'Different page'},
                        {**score(), 'score': float('nan')}, {**score(), 'version': 9}):
            result, job = self.assign()
            self.assertFalse(self.remote.finish(job['id'], payload))
            self.assertIsNone(result.result(1))
        result, job = self.assign()
        error = {'error': '没有听清声音，请靠近麦克风再读一次。'}
        self.assertTrue(self.remote.finish(job['id'], error))
        self.assertEqual(result.result(1), error)

    def test_reply_validation_and_worker_never_accept_paths_or_commands(self):
        self.assertTrue(valid_result(score(), TEXT))
        wrong = copy.deepcopy(score()); wrong['words'][0]['word'] = '<script>x</script>'
        self.assertFalse(valid_result(wrong, TEXT))
        for job in ({'id': 'a'*48, 'path': '/etc/passwd', 'text': TEXT},
                    {'id': 'a'*48, 'audio': '!!!!', 'text': TEXT},
                    {'id': 'a'*48, 'audio': base64.b64encode(b'a').decode(), 'text': '<script>x</script>'}):
            with self.assertRaises(ValueError): decode_job(job)
        for origin in ('http://raz.aotefan.com', 'https://a@raz.aotefan.com', 'https://raz.aotefan.com/path'):
            with self.assertRaises(ValueError): WorkerConnection(origin, TOKEN)

    def test_failed_delivery_and_shutdown_wake_up_waiting_assessment(self):
        result, job = self.assign()
        self.remote.abandon(job['id'])
        self.assertIsNone(result.result(1))
        result, _ = self.assign()
        self.remote.close()
        self.assertIsNone(result.result(1))

    def test_remote_results_use_original_account_and_version_guard(self):
        self.remote.lease_seconds, self.remote.task_seconds = 1, 2
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = Recordings(root / 'one'), Recordings(root / 'two')
            version = first.reserve('book', 1)
            first.save('book', 1, version, 'audio/wav', sample())
            service = Assessment(root, second, remote=self.remote)
            poll = self.ready()
            with patch.object(service, 'local_run', side_effect=AssertionError('Must use Mac')):
                result = self.pool.submit(service.run, 'book', 1, version, TEXT, first)
                job = poll.result(1)
                self.remote.finish(job['id'], score())
                self.assertEqual(result.result(1)['version'], version)
            self.assertEqual(first.list('book')['1']['assessment']['score'], 88)
            self.assertEqual(second.list('book'), {})
            # A fresh take requires work. Replace it again while Mac computes.
            version = first.reserve('book', 1)
            first.save('book', 1, version, 'audio/wav', sample())
            poll = self.ready()
            result = self.pool.submit(service.run, 'book', 1, version, TEXT, first)
            job = poll.result(1)
            latest = first.reserve('book', 1)
            first.save('book', 1, latest, 'audio/wav', sample(1))
            self.remote.finish(job['id'], score())
            with self.assertRaisesRegex(ValueError, '录音已更新'): result.result(1)
            self.assertIsNone(first.list('book')['1']['assessment'])
            service.close()

    def test_timeout_runs_existing_cloud_model_and_late_mac_cannot_replace_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); recordings = Recordings(root)
            version = recordings.reserve('book', 1)
            recordings.save('book', 1, version, 'audio/wav', sample())
            service = Assessment(root, recordings, remote=self.remote)
            poll = self.ready()
            with patch.object(service, 'local_run', return_value={**score(), 'score': 27}) as local:
                result = self.pool.submit(service.run, 'book', 1, version, TEXT)
                job = poll.result(1)
                self.assertEqual(result.result(1)['score'], 27)
                local.assert_called_once_with(sample(), TEXT)
            self.assertFalse(self.remote.finish(job['id'], score()))
            self.assertEqual(recordings.list('book')['1']['assessment']['score'], 27)
            self.assertEqual(self.remote.status()['counts']['local'], 1)
            service.close()


class WorkerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'manifest.json').write_text('{"books":[]}')
        (self.root / 'assessment-worker.key').write_text(TOKEN)
        self.remote = RemoteAssessment(TOKEN, poll_seconds=.02)
        started = threading.Event()
        owner = self
        class Server(ReaderHTTPServer):
            def __init__(self, address, handler):
                super().__init__(address, handler)
                owner.server = self
                started.set()
        self.patches = [patch('engine.server.ReaderHTTPServer', Server),
                        patch('engine.server.Assessment.warmup'),
                        patch('engine.assessment_remote.RemoteAssessment', return_value=self.remote)]
        for p in self.patches: p.start()
        self.thread = threading.Thread(target=serve, args=(self.root, 0, 'https://raz.test'), daemon=True)
        self.thread.start()
        self.assertTrue(started.wait(3))

    def tearDown(self):
        self.server.shutdown(); self.thread.join(3)
        for p in reversed(self.patches): p.stop()
        self.temporary.cleanup()

    def request(self, action='status', headers=None, body=b'{}'):
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)) as c:
            c.request('POST', '/api/assessment-worker/' + action, body,
                      {'Host': 'raz.test', 'Origin': 'https://raz.test', **(headers or {})})
            response = c.getresponse()
            return response.status, response.getheaders(), json.loads(response.read())

    def test_cookie_missing_wrong_token_and_cross_origin_cannot_claim_jobs(self):
        for headers in ({}, {'Cookie': '__Host-raz_session=browser'},
                        {'Authorization': 'Bearer ' + 'b'*64},
                        {'Authorization': 'Bearer ' + TOKEN, 'Origin': 'https://evil.test'}):
            self.assertEqual(self.request('poll', headers)[0], 403)
        self.assertFalse(self.remote.status()['ready'])
        self.assertEqual(list((self.root / 'visitors').iterdir()), [])

    def test_authorized_worker_has_no_guest_cookie_and_bounded_strict_requests(self):
        headers = {'Authorization': 'Bearer ' + TOKEN}
        status, response_headers, body = self.request(headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(any(k.lower() == 'set-cookie' for k, v in response_headers))
        self.assertFalse(body['ready'])
        self.assertEqual(self.request('poll', headers)[2], {'job': None})
        self.assertEqual(self.request('result', headers, b'{"id":"../../file"}')[0], 400)
        self.assertEqual(self.request('status', headers, b'x' * 1025)[0], 400)
        self.assertEqual(self.request('unknown', headers)[0], 404)
