#!/usr/bin/env python3
"""Outbound HTTPS accelerator. No listening port, SSH, or arbitrary file/command API."""
import argparse
import base64
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from engine.assessment import reference_text
from engine.assessment_process import AssessmentProcess
from engine.recordings import MAX_BYTES


class WorkerConnection:
    def __init__(self, origin, token, timeout=18):
        parsed = urlsplit(origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError('An HTTPS origin is required')
        self.origin, self.host, self.token, self.timeout = origin, parsed.hostname, token, timeout
        self.connection = None

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def request(self, action, body):
        if self.connection is None:
            self.connection = http.client.HTTPSConnection(self.host, timeout=self.timeout)
        try:
            self.connection.request('POST', '/api/assessment-worker/' + action,
                json.dumps(body, allow_nan=False).encode(), {
                    'Origin': self.origin, 'Authorization': 'Bearer ' + self.token,
                    'Content-Type': 'application/json', 'Accept-Encoding': 'identity'})
            response = self.connection.getresponse()
            limit = (MAX_BYTES * 4 // 3 + 16384) if action == 'poll' else 16384
            payload = response.read(limit + 1)
            if len(payload) > limit or response.status not in (200, 409):
                raise OSError('Worker endpoint unavailable')
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError('Invalid worker response')
            return value
        except (OSError, ValueError, http.client.HTTPException):
            self.close()
            raise


def decode_job(job):
    if (not isinstance(job, dict) or set(job) != {'id', 'text', 'audio'}
            or not isinstance(job['id'], str) or not re.fullmatch(r'[a-f0-9]{48}', job['id'])
            or not isinstance(job['audio'], str) or len(job['audio']) > (MAX_BYTES + 2) // 3 * 4):
        raise ValueError('Invalid job')
    text = reference_text(job['text'])
    if text != job['text']:
        raise ValueError('Noncanonical reference')
    audio = base64.b64decode(job['audio'], validate=True)
    if not 0 < len(audio) <= MAX_BYTES:
        raise ValueError('Invalid audio size')
    return audio, text


def heartbeat_loop(client, job_id, stopped):
    try:
        # Short takes usually finish before this; avoid an unnecessary TLS request.
        if stopped.wait(.6):
            return
        while not stopped.is_set():
            try:
                if not client.request('heartbeat', {'id': job_id}).get('accepted'):
                    return
            except (OSError, ValueError, http.client.HTTPException):
                pass  # The cloud's short lease controls failover, even during network loss.
            if stopped.wait(.8):
                return
    finally:
        client.close()


def run(config_path):
    if config_path.stat().st_mode & 0o077:
        raise ValueError('Worker config must have mode 0600')
    config = json.loads(config_path.read_text())
    if not re.fullmatch(r'[a-f0-9]{64}', config.get('token', '')):
        raise ValueError('Invalid credential')
    directory = config_path.parent
    os.umask(0o077)
    jobs = directory / 'jobs'
    jobs.mkdir(mode=0o700, exist_ok=True)
    # Only our private, interrupted job directories; never the user's reading data.
    for path in jobs.glob('job-*'):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
    logger = logging.getLogger('mac-scorer')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(directory / 'worker.log', maxBytes=512 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    client = WorkerConnection(config['origin'], config['token'])
    model = AssessmentProcess([str(APP / '.venv-assessment/bin/python'),
                               str(APP / 'scripts/assessment_worker.py'), '--serve'], directory, timeout=22)
    ready, backoff = False, 1
    try:
        while True:
            try:
                if not ready:
                    if model.call({'warmup': True}) != {'ready': True}:
                        raise RuntimeError('Model not ready')
                    ready = True
                    logger.info('model_ready')
                response = client.request('poll', {})
                backoff = 1
                job = response.get('job')
                if job is None:
                    time.sleep(.1)
                    continue
                audio, text = decode_job(job)
                heartbeat = WorkerConnection(config['origin'], config['token'], timeout=2)
                stopped = threading.Event()
                thread = threading.Thread(target=heartbeat_loop,
                    args=(heartbeat, job['id'], stopped), daemon=True)
                thread.start()
                started = time.monotonic()
                try:
                    with tempfile.TemporaryDirectory(prefix='job-', dir=jobs) as temporary:
                        path = Path(temporary) / 'recording.audio'
                        path.write_bytes(audio)
                        try:
                            result = model.call({'path': str(path), 'text': text})
                        except RuntimeError:
                            ready = False
                            result = {'worker_error': True}
                        model_seconds = time.monotonic() - started
                        accepted = client.request('result', {'id': job['id'], 'result': result}).get('accepted')
                        logger.info('job_%s model=%.3fs roundtrip=%.3fs',
                                    'accepted' if accepted else 'expired', model_seconds,
                                    time.monotonic() - started)
                finally:
                    stopped.set()
                    thread.join(2.2)
                    del audio, job
            except (OSError, ValueError, RuntimeError, http.client.HTTPException):
                # Do not log request URLs, credentials, reference words, or audio.
                logger.info('connection_or_model_unavailable retry_in=%ss', backoff)
                client.close()
                time.sleep(backoff)
                backoff = min(15, backoff * 2)
    except KeyboardInterrupt:
        logger.info('worker_stopped')
    finally:
        client.close()
        model.close()
        handler.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    run(args.config.resolve())
