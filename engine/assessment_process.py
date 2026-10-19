"""One offline model process per reader server, communicating only over pipes."""
import json
import os
import selectors
import subprocess
import threading
import time


class AssessmentProcess:
    def __init__(self, command, directory, timeout=120):
        self.command, self.directory, self.timeout = command, directory, timeout
        self.lock = threading.Lock()
        self.process = None
        self.closed = False

    def _stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()

    def call(self, request):
        with self.lock:
            if self.closed:
                raise RuntimeError('评测服务已关闭。')
            try:
                if self.process is None or self.process.poll() is not None:
                    self._stop()
                    self.directory.mkdir(parents=True, exist_ok=True)
                    # A file avoids a full stderr pipe blocking the model. No audio/text logged.
                    with (self.directory / 'assessment-worker.log').open('wb') as log:
                        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=log)
                process = self.process
                process.stdin.write((json.dumps(request) + '\n').encode())
                process.stdin.flush()
                deadline, output = time.monotonic() + self.timeout, bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while b'\n' not in output:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0 or not selector.select(remaining):
                            raise TimeoutError('Assessment process timed out')
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            raise OSError('Assessment process exited')
                        output.extend(chunk)
                        if len(output) > 1024 * 1024:
                            raise ValueError('Assessment response too large')
                result = json.loads(output)
                if not isinstance(result, dict):
                    raise ValueError('Invalid assessment response')
                if result.get('worker_error'):
                    raise RuntimeError('本机评测暂不可用，请重试。')
                return result
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                # A timeout/exit must not poison the next request or deliver a late response.
                self._stop()
                raise RuntimeError('本机评测超时或暂不可用，请重试。') from error
