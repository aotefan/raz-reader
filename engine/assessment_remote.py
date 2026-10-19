"""One authenticated, outbound-polling accelerator; cloud remains the fallback.

Jobs exist only in memory and contain no account, book, page, or filesystem IDs.
Only Assessment.run may persist a result, using the original recording version.
"""
import base64
import hmac
import json
import math
import re
import secrets
import threading
import time

MAX_RESULT = 1024 * 1024


def valid_result(result, reference):
    from engine.assessment import METHOD
    if not isinstance(result, dict):
        return False
    try:
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_RESULT:
            return False
    except (ValueError, TypeError):
        return False
    # Input-quality failures are authoritative too; infrastructure failures fall back.
    errors = {
        '这段录音超过 60 秒，请分段练习后再评测。',
        '录音太短，请完整读完本页后再试。',
        '没有听清声音，请靠近麦克风再读一次。',
        '录音声音失真，请离麦克风稍远一点再试。',
        '这次没听清英文，请在安静处再读一次。',
    }
    if set(result) == {'error'}:
        return isinstance(result['error'], str) and result['error'] in errors
    required = {'score', 'method', 'reference_only', 'duration', 'reference', 'words',
                'phone_error_rate', 'weighted_phone_error_rate', 'heard_phones', 'offline'}
    if set(result) != required:
        return False
    if (result['method'] != METHOD or result['reference'] != reference
            or result['reference_only'] is not True or result['offline'] is not True
            or type(result['score']) is not int or not 0 <= result['score'] <= 100):
        return False
    for key in ('duration', 'phone_error_rate', 'weighted_phone_error_rate'):
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            return False
    if not .4 <= result['duration'] <= 60:
        return False
    def text(value, maximum=1024):
        return isinstance(value, str) and len(value) <= maximum and not re.search(r'[<>\x00-\x1f]', value)
    words = result['words']
    if not isinstance(words, list) or not 1 <= len(words) <= 120:
        return False
    for i, word in enumerate(words):
        if (not isinstance(word, dict)
                or set(word) != {'word', 'position', 'status', 'expected', 'heard', 'phones'}
                or type(word['position']) is not int or word['position'] != i
                or word['status'] not in ('matched', 'practice')
                or not text(word['word']) or not text(word['expected'])
                or (word['heard'] is not None and not text(word['heard']))
                or not isinstance(word['phones'], list) or len(word['phones']) > 512):
            return False
        for phone in word['phones']:
            if (not isinstance(phone, dict) or set(phone) != {'expected', 'heard', 'confidence'}
                    or not text(phone['expected']) or not text(phone['heard'])
                    or type(phone['confidence']) not in (int, float)
                    or not 0 <= phone['confidence'] <= 1):
                return False
    heard = result['heard_phones']
    return isinstance(heard, list) and len(heard) <= 10000 and all(text(p) for p in heard)


class RemoteAssessment:
    def __init__(self, token, poll_seconds=10, lease_seconds=3, task_seconds=None):
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            raise ValueError('Invalid assessment worker credential')
        self.token = token
        self.poll_seconds, self.lease_seconds = poll_seconds, lease_seconds
        self.task_seconds = task_seconds
        self.condition = threading.Condition()
        self.waiter = self.job = None
        self.closed = False
        self.counts = dict(remote=0, local=0, unavailable=0, timeout=0, failed=0)

    def authorized(self, authorization):
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization.encode(), ('Bearer ' + self.token).encode())

    def status(self):
        with self.condition:
            return {'ready': self.waiter is not None, 'busy': self.job is not None,
                    'counts': dict(self.counts)}

    def record_local(self):
        with self.condition:
            self.counts['local'] += 1

    def poll(self):
        with self.condition:
            if self.closed or self.waiter is not None or self.job is not None:
                return None
            waiter = {}
            self.waiter = waiter
            self.condition.notify_all()
            try:
                self.condition.wait_for(lambda: 'job' in waiter or self.closed, self.poll_seconds)
                job = waiter.get('job')
                if job is None or self.job is not job:
                    return None
                return {'id': job['id'], 'text': job['text'],
                        'audio': base64.b64encode(job['audio']).decode('ascii')}
            finally:
                if self.waiter is waiter:
                    self.waiter = None

    def heartbeat(self, job_id):
        with self.condition:
            job = self._active(job_id)
            if job is None:
                return False
            job['lease'] = time.monotonic() + self.lease_seconds
            self.condition.notify_all()
            return True

    def _active(self, job_id):
        job = self.job
        if (not job or job['id'] != job_id or 'result' in job or job.get('failed')
                or time.monotonic() >= min(job['lease'], job['deadline'])):
            return None
        return job

    def finish(self, job_id, result):
        with self.condition:
            job = self._active(job_id)
            if job is None:
                return False  # Late replies can never overwrite cloud/new-take results.
            if valid_result(result, job['text']):
                job['result'] = result
            else:
                job['failed'] = True
            self.condition.notify_all()
            return not job.get('failed', False)

    def abandon(self, job_id):
        with self.condition:
            if self.job and self.job['id'] == job_id:
                self.job['failed'] = True
                self.condition.notify_all()

    def try_run(self, audio, text):
        with self.condition:
            if self.closed or self.waiter is None or self.job is not None:
                self.counts['unavailable'] += 1
                return None
            now = time.monotonic()
            budget = self.task_seconds or min(20, max(5, 3 + len(text.split()) * .12))
            job = {'id': secrets.token_hex(24), 'audio': audio, 'text': text,
                   'lease': now + self.lease_seconds, 'deadline': now + budget}
            self.job = job
            self.waiter['job'] = job
            self.waiter = None
            self.condition.notify_all()
            try:
                while not self.closed and not job.get('failed') and 'result' not in job:
                    remaining = min(job['lease'], job['deadline']) - time.monotonic()
                    if remaining <= 0:
                        break
                    self.condition.wait(remaining)
                if 'result' in job:
                    self.counts['remote'] += 1
                    return job['result']
                self.counts['failed' if job.get('failed') else 'timeout'] += 1
                return None
            finally:
                self.job = None
                job.pop('audio', None)
                self.condition.notify_all()

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
