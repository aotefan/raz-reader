"""Loopback-only reader. IDs resolve to a fixed manifest allowlist, never URL paths."""
import argparse
import json
import mimetypes
import re
import sqlite3
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit
from engine.pipeline import APP, connect, contained, digest
from engine.recordings import MAX_BYTES, Recordings
from engine.assessment import Assessment, reference_text
from engine.dictionary import lookup
from engine.language import LanguageTools
from engine.page_text import PageText
from engine.sentence_audio import render_original_clip


class ReaderHTTPServer(ThreadingHTTPServer):
    # Browsers open several module/font/PDF requests together. The stdlib default
    # backlog of five caused module connection resets and an empty shelf on macOS.
    request_queue_size = 128

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


def serve(data, port, cloud_origin=None, listen='127.0.0.1', manifest_path=None):
    data = Path(data).resolve()
    manifest = json.loads((Path(manifest_path) if manifest_path else data / 'manifest.json').read_text())
    books = {b['book_id']: b for b in manifest['books']}
    editions = {b['edition_id']: b for b in manifest['books']}
    recordings = Recordings(data)
    assessment = Assessment(data, recordings, timeout=150 if cloud_origin else 120)
    language = LanguageTools(data)
    page_text = PageText(data)
    cloud = None
    if cloud_origin:
        from engine.cloud_sessions import CloudSessions
        cloud = CloudSessions(data, cloud_origin)
        worker_key = data / 'assessment-worker.key'
        if worker_key.is_file():
            from engine.assessment_remote import RemoteAssessment
            assessment.remote = RemoteAssessment(worker_key.read_text().strip())
    audio = {}
    for b in books.values():
        assets = list(b['audio']['page_map'].values())
        if b['audio'].get('whole_book'):
            assets.append(b['audio']['whole_book'])
        for a in assets:
            audio[a['asset_id']] = (a, b)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(20)
            self.data, self.recordings, self.session_cookie = data, recordings, None
            self.reader_scope, self.account = 'local', None
            self.extra_cookies = []

        def log_message(self, fmt, *args):
            if cloud:
                return  # No visitor identifiers, words or recording URLs in logs.
            if len(args) > 1 and str(args[1]) not in ('200', '206', '304'):
                super().log_message(fmt, *args)

        def allowed(self):
            if cloud:
                origin = self.headers.get('Origin')
                if (self.headers.get('Host') != cloud.host
                        or origin not in (None, cloud.origin)
                        or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                        or (self.command == 'POST' and origin != cloud.origin)):
                    return False
                route = urlsplit(self.path).path
                if route == '/api/health' and self.command in ('GET', 'HEAD'):
                    return True
                identity = self.headers.get('X-Real-IP', self.client_address[0])
                if not cloud.rate(identity, 'requests', 400, 60):
                    return False
                create = route == '/api/library' and self.command in ('GET', 'HEAD')
                directory, cookie = cloud.resolve(self.headers.get('Cookie'), create=False)
                if directory is None and create and cloud.rate(identity, 'new-session', 30, 3600):
                    try:
                        directory, cookie = cloud.resolve(None, create=True)
                    except RuntimeError:
                        return False
                if directory is None:
                    return False
                if self.command == 'POST' and not cloud.rate(directory.name, route.split('/')[2], 90, 60):
                    return False
                self.data, self.recordings, self.session_cookie = directory, Recordings(directory), cookie
                self.account = cloud.accounts.info(directory)
                self.reader_scope = cloud.scope(directory)
                supplied = self.headers.get('X-Reader-Scope')
                if route.startswith('/api/recordings/') and self.command in ('GET', 'HEAD'):
                    supplied = parse_qs(urlsplit(self.path).query).get('scope', [supplied])[0]
                private = route.startswith(('/api/recordings/', '/api/account/')) or route == '/api/assessment'
                if supplied is not None and supplied != self.reader_scope:
                    return False
                if self.account and private and supplied != self.reader_scope:
                    return False
                return True
            if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
                return False
            origin = self.headers.get('Origin')
            return origin is None or origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

        def respond_headers(self, status, content_type, length, extra=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            if length is None:
                # A saved acknowledgement is flushed before the score is ready.
                # Closing the response delimits the final JSON without buffering it.
                self.send_header('Connection', 'close')
                self.close_connection = True
            else:
                self.send_header('Content-Length', str(length))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            if self.session_cookie:
                self.send_header('Set-Cookie', self.session_cookie)
            for cookie in self.extra_cookies:
                self.send_header('Set-Cookie', cookie)
            self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
            self.send_header('Permissions-Policy', 'microphone=(self)')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; worker-src 'self' blob:; connect-src 'self' blob:; font-src 'self' data:; object-src 'none'; frame-ancestors 'self'; base-uri 'self'; form-action 'none'")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def json(self, obj, status=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.respond_headers(status, 'application/json; charset=utf-8', len(body))
            if self.command != 'HEAD':
                self.wfile.write(body)

        def assessment_worker(self, route):
            # Dedicated machine credential, never a browser cookie or guest allocation.
            remote = assessment.remote
            if (not cloud or not remote or self.headers.get('Host') != cloud.host
                    or self.headers.get('Origin') != cloud.origin
                    or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                    or not remote.authorized(self.headers.get('Authorization'))):
                self.close_connection = True
                return self.json({'error': 'Worker authentication required'}, 403)
            identity = self.headers.get('X-Real-IP', self.client_address[0])
            if not cloud.rate(identity, 'assessment-worker', 240, 60):
                self.close_connection = True
                return self.json({'error': 'Worker request limit'}, 429)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                limit = 1024 * 1024 if route.endswith('/result') else 1024
                if self.headers.get('Transfer-Encoding') or not 0 < length <= limit:
                    raise ValueError('Invalid worker body length')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected object')
                if route == '/api/assessment-worker/status':
                    return self.json(remote.status())
                if route == '/api/assessment-worker/poll':
                    job = remote.poll()
                    try:
                        return self.json({'job': job})
                    except OSError:
                        if job:
                            remote.abandon(job['id'])
                        return
                if route in ('/api/assessment-worker/heartbeat', '/api/assessment-worker/result'):
                    job_id = body.get('id')
                    if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{48}', job_id):
                        raise ValueError('Invalid job')
                    accepted = (remote.heartbeat(job_id) if route.endswith('/heartbeat')
                                else remote.finish(job_id, body.get('result')))
                    return self.json({'accepted': accepted}, 200 if accepted else 409)
                return self.json({'error': 'Unknown worker endpoint'}, 404)
            except (ValueError, TypeError):
                self.close_connection = True
                return self.json({'error': 'Invalid worker request'}, 400)

        def saved_assessment(self, edition, page, version, reference):
            self.respond_headers(200, 'application/json; charset=utf-8', None,
                                 {'X-Recording-Saved': str(version)})
            self.wfile.write(b'{"saved":true,"stale":false,')
            self.wfile.flush()
            try:
                text = reference_text(unquote(reference, encoding='utf-8', errors='strict'))
                result = {'assessment': assessment.run(edition, page, version, text,
                                                       recordings=self.recordings)}
            except (ValueError, RuntimeError) as error:
                # Scoring failure never turns a successfully saved take into a failed upload.
                result = {'assessment_error': str(error)}
            except (OSError, sqlite3.Error):
                result = {'assessment_error': '录音已保存，评测暂不可用，请重试评测。'}
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode()[1:])
            self.wfile.flush()

        def file(self, path):
            length = path.stat().st_size
            start, end, status = 0, length - 1, 200
            value = self.headers.get('Range')
            if value:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
                if not match or not any(match.groups()):
                    return self.json({'error': 'Invalid range'}, 416)
                a, b = match.groups()
                if not a:
                    start = max(0, length - int(b))
                else:
                    start, end = int(a), min(int(b) if b else end, end)
                if start > end or start >= length:
                    return self.json({'error': 'Range outside file'}, 416)
                status = 206
            extra = {'Accept-Ranges': 'bytes'}
            if status == 206:
                extra['Content-Range'] = f'bytes {start}-{end}/{length}'
            content_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            if path.suffix == '.mjs':
                content_type = 'text/javascript'
            self.respond_headers(status, content_type, end - start + 1, extra)
            if self.command == 'HEAD':
                return
            with path.open('rb') as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_HEAD(self):
            self.do_GET()

        def recording_route(self, route):
            parts = route.removeprefix('/api/recordings/').split('/')
            b = editions.get(parts[0])
            if not b or len(parts) > 3:
                raise ValueError('Unknown recording edition')
            page = int(parts[1]) if len(parts) > 1 else None
            if page is not None and not 1 <= page <= b['page_count']:
                raise ValueError('Invalid recording page')
            if len(parts) == 3 and parts[2] != 'take':
                raise ValueError('Unknown recording endpoint')
            return b['edition_id'], page, len(parts) == 3

        def recording_audio(self, mime, audio):
            length = len(audio)
            start, end, status = 0, length - 1, 200
            value = self.headers.get('Range')
            if value:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
                if not match or not any(match.groups()):
                    return self.json({'error': 'Invalid range'}, 416)
                a, b = match.groups()
                if not a:
                    start = max(0, length - int(b))
                else:
                    start, end = int(a), min(int(b) if b else end, end)
                if start > end or start >= length:
                    return self.json({'error': 'Range outside recording'}, 416)
                status = 206
            extra = {'Accept-Ranges': 'bytes'}
            if status == 206:
                extra['Content-Range'] = f'bytes {start}-{end}/{length}'
            self.respond_headers(status, mime, end - start + 1, extra)
            if self.command != 'HEAD':
                self.wfile.write(audio[start:end + 1])

        def do_GET(self):
            if not self.allowed():
                return self.json({'error': '请刷新网页重试；仅允许本站请求。'}, 403)
            route = urlsplit(self.path).path

            try:
                if cloud and route == '/api/health':
                    return self.json({'status': 'ok', 'mode': 'cloud_full', 'books': len(books)})
                if route.startswith('/api/sentence-audio/'):
                    parts = route.removeprefix('/api/sentence-audio/').split('/')
                    book = editions.get(parts[0])
                    if len(parts) != 2 or not book:
                        return self.json({'error': 'Unknown sentence'}, 404)
                    sentence = next((s for s in book['audio'].get('sentences', []) if s['id'] == parts[1]), None)
                    if sentence is None:
                        return self.json({'error': 'No verified original sentence'}, 404)
                    try:
                        return self.recording_audio('audio/wav', render_original_clip(book, sentence, data))
                    except ValueError as error:
                        return self.json({'error': str(error)}, 409)
                    except (subprocess.SubprocessError, OSError):
                        return self.json({'error': '原声片段暂不可用，请重试。'}, 503)
                if route == '/api/dictionary':
                    word = parse_qs(urlsplit(self.path).query).get('word', [''])[0]
                    try:
                        return self.json(lookup(APP / 'data/dictionary/ecdict.sqlite3', word))
                    except ValueError as error:
                        return self.json({'error': str(error)}, 400)
                if route.startswith('/api/recordings/'):
                    edition, page, take = self.recording_route(route)
                    if take:
                        return self.json({'error': 'Unknown endpoint'}, 404)
                    if page is None:
                        return self.json({'pages': self.recordings.list(edition)})
                    recording = self.recordings.playback(edition, page)
                    if not recording:
                        return self.json({'error': 'No saved recording'}, 404)
                    return self.recording_audio(*recording)
                if route == '/api/library':
                    assessment_summaries = self.recordings.summaries({b['edition_id']: b['page_count'] for b in books.values()})
                    db = connect(self.data)
                    db.execute('BEGIN')
                    progress = {r[0]: {'page': r[1], 'completed': bool(r[2]), 'updated_at': r[3], 'visited': True}
                                for r in db.execute('SELECT * FROM progress')}
                    writers = {}
                    for edition_id, writer_id, last_sequence in db.execute('SELECT * FROM progress_writers'):
                        writers.setdefault(edition_id, {})[writer_id] = last_sequence
                    db.commit()
                    db.close()
                    # No absolute private paths or unverified stream refs in browser API.
                    public = []
                    for b in books.values():
                        rec = {k: b[k] for k in ['book_id', 'edition_id', 'level', 'title', 'title_status',
                                                'demo', 'page_count', 'validation', 'authorization', 'reader_source']}
                        rec['pdf_url'] = '/api/pdf/' + b['book_id']
                        # User enabled local text processing for all imported books.
                        # PDF permission flags remain recorded as source metadata.
                        rec['text_lookup_allowed'] = True
                        rec['local_text_processing'] = 'user_enabled'
                        rec['progress'] = progress.get(b['edition_id'], {'page': 1, 'completed': False})
                        rec['assessment_summary'] = assessment_summaries.get(b['edition_id'])
                        rec['acknowledged_writes'] = writers.get(b['edition_id'], {})
                        rec['audio'] = {**b['audio'], 'page_map': {
                            page: {'url': '/api/audio/' + a['asset_id'], 'duration': a['duration'],
                                   'origin': 'pdf_embedded' if a.get('storage') == 'app_embedded' else 'verified_external'}
                            for page, a in b['audio']['page_map'].items()}}
                        whole = b['audio'].get('whole_book')
                        rec['audio']['whole_book'] = ({'url': '/api/audio/' + whole['asset_id'],
                                                      'duration': whole['duration'],
                                                      'timeline': [{k: cue[k] for k in ('page', 'start', 'end')}
                                                                   for cue in whole.get('timeline', [])]} if whole else None)
                        rec['audio']['sentences'] = [{**sentence,
                            'url': '/api/sentence-audio/' + b['edition_id'] + '/' + sentence['id']}
                            for sentence in b['audio'].get('sentences', [])]
                        rec['audio_detection'] = b.get('audio_detection', {}).get('status', 'unknown')
                        rec['audio_embedded_stream_count'] = b.get('audio_detection', {}).get('embedded_stream_count', 0)
                        rec['audio']['embedded'] = {
                            'pages': sorted({p for annotation in b.get('audio_detection', {}).get('screen_annotations', [])
                                             if annotation.get('embedded_stream_refs') and annotation.get('media_type') in ('u:audio/mpeg', 'audio/mpeg')
                                             for p in annotation.get('pages', [])}),
                            'rich_media_pages': sorted({item.get('page') for item in b.get('audio_detection', {}).get('rich_media_audio', [])
                                                        if isinstance(item.get('page'), int)}),
                            'mode': 'pdfjs_native_annotation', 'exported': False,
                            'verification': 'original_pdf_page_association_not_independent_content_review'}
                        public.append(rec)
                        if cloud:
                            # Prebuilt allowlist keeps every private path out of the response.
                            safe = b['cloud_public'].copy()
                            safe.update({k: rec[k] for k in ('progress', 'assessment_summary', 'acknowledged_writes')})
                            public[-1] = safe
                    return self.json({'levels': manifest['levels'], 'scope': manifest.get('scope'), 'books': public,
                                      'errors': [] if cloud else manifest['errors'],
                                      'reader_scope': self.reader_scope, 'account': self.account,
                                      'saved_accounts': ([{'username': item['username'], 'display_name': item['display_name'],
                                        'active': bool(self.account and item['username'] == self.account['username'])}
                                        for item in cloud.remembered(self.headers.get('Cookie'))] if cloud else []),
                                      **({'mode': 'cloud_full', 'accounts_enabled': True} if cloud else {})})
                if route.startswith('/api/page-text/'):
                    parts = route.removeprefix('/api/page-text/').split('/')
                    b = books.get(parts[0])
                    if len(parts) != 2 or not b or b['validation'] == 'failed':
                        return self.json({'error': 'Unknown or invalid book'}, 404)
                    try:
                        return self.json(page_text.read(b, int(parts[1])))
                    except RuntimeError as error:
                        return self.json({'error': str(error)}, 503)
                if route.startswith('/api/pdf/'):
                    b = books.get(route.removeprefix('/api/pdf/'))
                    if not b or b['validation'] == 'failed':
                        return self.json({'error': 'Unknown or invalid book'}, 404)
                    p = contained(b['source_path'], b['source_root'])
                    return self.file(p)
                if route.startswith('/api/audio/'):
                    found = audio.get(route.removeprefix('/api/audio/'))
                    if not found:
                        return self.json({'error': 'No verified audio'}, 404)
                    a, b = found
                    if a.get('storage') == 'app_embedded':
                        p = contained(data / a['file'], data / 'embedded-audio' / b['edition_id'])
                    else:
                        p = contained(Path(b['source_root']) / a['file'], b['source_root'])
                    if digest(p) != a['sha256']:
                        return self.json({'error': 'Verified asset changed; reindex required'}, 409)
                    return self.file(p)
                if cloud:
                    return self.json({'error': 'Unknown endpoint'}, 404)
                return self.file(contained(APP / 'web' / (route.lstrip('/') or 'index.html'), APP / 'web'))
            except (BrokenPipeError, ConnectionResetError):
                pass
            except (OSError, ValueError):
                return self.json({'error': 'Not found or outside allowed root'}, 404)

        def do_POST(self):
            route = urlsplit(self.path).path
            if route.startswith('/api/assessment-worker/'):
                return self.assessment_worker(route)
            if not self.allowed():
                self.close_connection = True
                return self.json({'error': '请刷新网页重试；仅允许本站请求。'}, 403)
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self.json({'error': 'Content-Length required'}, 400)
            route = urlsplit(self.path).path
            if route.startswith('/api/account/') and cloud:
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        self.close_connection = True
                        raise ValueError('请求内容过长，请重试。')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('请求格式不正确。')
                    action = route.removeprefix('/api/account/')
                    identity = self.headers.get('X-Real-IP', self.client_address[0])
                    remembered = cloud.remembered(self.headers.get('Cookie'))
                    if action in ('register', 'login'):
                        if (not cloud.rate(identity, 'auth-' + action, 8 if action == 'register' else 15, 3600 if action == 'register' else 300)
                                or not cloud.rate(str(body.get('username', '')).lower()[:32], 'auth-user', 20, 300)):
                            return self.json({'error': '尝试过于频繁，请稍后再试。'}, 429)
                        username, _ = cloud.accounts.credentials(body.get('username'), body.get('password'))
                        if len(remembered) >= 8 and not any(item['username'] == username for item in remembered):
                            raise ValueError('此设备已保存 8 个账号，请先移除一个再添加。')
                        if action == 'register':
                            if self.account and body.get('import_progress') is True:
                                raise ValueError('不能把当前孩子的记录带入另一个账号。')
                            level = body.get('level', 'all')
                            if level not in ['all'] + [l['level'] for l in manifest['levels']]:
                                level = 'all'
                            _, token = cloud.accounts.register(body.get('username'), body.get('password'),
                                lambda: cloud.resolve(None, create=True)[0],
                                claim=self.data if body.get('import_progress') is True else None, level=level,
                                display_name=body.get('display_name'))
                        else:
                            _, token = cloud.accounts.login(body.get('username'), body.get('password'))
                        for item in remembered:
                            if item['username'] == username:
                                cloud.accounts.logout(item['token'])
                        remembered = [{'username': username, 'token': token},
                                      *[item for item in remembered if item['username'] != username]]
                        self.session_cookie = cloud.account_cookie(token)
                        self.extra_cookies.append(cloud.remembered_cookie(remembered))
                    elif action == 'switch':
                        target = next((item for item in remembered if item['username'] == body.get('username')), None)
                        if not target:
                            raise ValueError('请先登录这个账号，登录后才能快捷切换。')
                        self.session_cookie = cloud.account_cookie(target['token'])
                        self.extra_cookies.append(cloud.remembered_cookie(remembered))
                    elif action in ('logout', 'forget'):
                        username = self.account['username'] if action == 'logout' and self.account else body.get('username')
                        target = next((item for item in remembered if item['username'] == username), None)
                        if not target:
                            raise ValueError('这个账号已从此设备移除，请刷新后重试。')
                        if self.account and username == self.account['username']:
                            # Allocate before revoking, so a full guest quota cannot strand this session.
                            _, self.session_cookie = cloud.resolve(None, create=True)
                        cloud.accounts.logout(target['token'])
                        self.extra_cookies.append(cloud.remembered_cookie([item for item in remembered if item['username'] != username]))
                    elif action == 'profile':
                        if not self.account:
                            raise ValueError('请先登录后再修改昵称。')
                        cloud.accounts.rename(self.data, body.get('display_name'))
                    elif action == 'preferences':
                        level = body.get('level')
                        if not self.account or level not in ['all'] + [l['level'] for l in manifest['levels']]:
                            raise ValueError('请选择有效等级。')
                        cloud.accounts.level(self.data, level)
                    else:
                        return self.json({'error': 'Unknown endpoint'}, 404)
                    return self.json({'saved': True})
                except (ValueError, TypeError) as error:
                    return self.json({'error': str(error)}, 400)
                except (RuntimeError, OSError, sqlite3.Error):
                    return self.json({'error': '账号服务暂时繁忙，请稍后重试。'}, 503)
            if route == '/api/assessment':
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 8192:
                        self.close_connection = True
                        raise ValueError('Invalid body length')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('Expected JSON object')
                    edition, page = body.get('edition_id'), body.get('page')
                    book = editions.get(edition) if isinstance(edition, str) else None
                    if not book or type(page) is not int or not 1 <= page <= book['page_count']:
                        raise ValueError('Invalid edition or page')
                    return self.json(assessment.run(edition, page, body.get('version'), body.get('text'),
                                                    recordings=self.recordings))
                except (BrokenPipeError, ConnectionResetError):
                    return
                except (ValueError, TypeError) as error:
                    return self.json({'error': str(error)}, 400)
                except RuntimeError as error:
                    return self.json({'error': str(error)}, 503)
            if route in ('/api/translate', '/api/pronounce', '/api/speak'):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 4096:
                        self.close_connection = True
                        raise ValueError('Invalid body length')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError('Expected a JSON object')
                    if route == '/api/translate':
                        return self.json(language.translate(body.get('text')))
                    if route == '/api/pronounce':
                        audio_bytes = language.pronounce(body.get('word'))
                        self.respond_headers(200, 'audio/wav', len(audio_bytes), {'X-Audio-Source': 'local-generated-word'})
                        return self.wfile.write(audio_bytes)
                    return self.json({'error': '原声模式不生成语音；请使用已核验的原配音。'}, 409)
                except (BrokenPipeError, ConnectionResetError):
                    return
                except (ValueError, TypeError) as error:
                    return self.json({'error': str(error)}, 400)
                except RuntimeError as error:
                    return self.json({'error': str(error)}, 503)
            if route.startswith('/api/recordings/'):
                try:
                    edition, page, take = self.recording_route(route)
                    if page is None:
                        raise ValueError('Recording page required')
                    length = int(self.headers.get('Content-Length', '0'))
                    if take:
                        if length != 0:
                            raise ValueError('Expected empty take request')
                        return self.json({'version': self.recordings.reserve(edition, page)})
                    if not 0 < length <= MAX_BYTES:
                        self.close_connection = True
                        raise ValueError('Recording must be between 1 byte and 20 MiB')
                    version = int(self.headers.get('X-Recording-Version', '0'))
                    reference = self.headers.get('X-Assessment-Text')
                    if reference is not None and len(reference) > 8192:
                        raise ValueError('Assessment reference header too large')
                    payload = self.rfile.read(length)
                    if len(payload) != length:
                        raise ValueError('Incomplete recording')
                    args = (edition, page, version, self.headers.get('Content-Type', ''), payload)
                    saved = cloud.save(self.recordings, *args) if cloud else self.recordings.save(*args)
                    if saved and reference is not None:
                        try:
                            return self.saved_assessment(edition, page, version, reference)
                        except OSError:
                            return  # Disconnected receiver; never write a second HTTP response.
                    return self.json({'saved': saved, 'stale': not saved})
                except (BrokenPipeError, ConnectionResetError):
                    return
                except (ValueError, TypeError) as error:
                    self.close_connection = True
                    return self.json({'error': str(error)}, 400)
                except (OSError, sqlite3.Error):
                    return self.json({'error': 'Could not save local recording'}, 503)
            if urlsplit(self.path).path != '/api/progress':
                return self.json({'error': 'Unknown endpoint'}, 404)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('Invalid body length')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
                supplied_scope = body.get('reader_scope')
                if cloud and ((supplied_scope is not None and supplied_scope != self.reader_scope)
                              or (self.account and supplied_scope != self.reader_scope)):
                    return self.json({'error': '账号已切换，请刷新网页。'}, 409)
                b = editions.get(body.get('edition_id'))
                page = body.get('page')
                if not b or type(page) is not int or not 1 <= page <= b['page_count']:
                    raise ValueError('Invalid edition or page')
                completed = body.get('completed', False)
                if type(completed) is not bool:
                    raise ValueError('Invalid completion flag')
                writer, sequence = body.get('writer_id'), body.get('sequence')
                if 'writer_id' in body or 'sequence' in body:
                    if (not isinstance(writer, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', writer)
                            or type(sequence) is not int or not 1 <= sequence <= 9007199254740991):
                        raise ValueError('Invalid writer or sequence')
                db = connect(self.data)
                try:
                    db.execute('BEGIN IMMEDIATE')
                    if writer is not None:
                        previous = db.execute('SELECT last_sequence FROM progress_writers WHERE edition_id=? AND writer_id=?',
                                              (b['edition_id'], writer)).fetchone()
                        if previous and sequence <= previous[0]:
                            db.rollback()
                            return self.json({'saved': False, 'stale': True})
                        db.execute('''INSERT INTO progress_writers VALUES (?, ?, ?)
                          ON CONFLICT(edition_id, writer_id) DO UPDATE SET last_sequence=excluded.last_sequence''',
                                   (b['edition_id'], writer, sequence))
                    db.execute('''INSERT INTO progress VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                      ON CONFLICT(edition_id) DO UPDATE SET page=excluded.page,
                        completed=MAX(progress.completed, excluded.completed), updated_at=excluded.updated_at''',
                               (b['edition_id'], page, int(completed)))
                    db.commit()
                finally:
                    db.close()
                return self.json({'saved': True})
            except (ValueError, TypeError, json.JSONDecodeError) as e:
                return self.json({'error': str(e)}, 400)

    if listen != '127.0.0.1' and not cloud:
        raise ValueError('Non-loopback binding requires cloud sessions')
    server = ReaderHTTPServer((listen, port), Handler)
    assessment.warmup()
    print(f'RAZ reader: http://127.0.0.1:{port} (Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        assessment.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, default=APP / 'data')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--cloud-origin')
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    serve(args.data, args.port, args.cloud_origin, args.listen, args.manifest)
