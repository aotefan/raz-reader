"""User microphone recordings only. One atomic, locally stored take per edition/page."""
from contextlib import contextmanager
import json
import math
import sqlite3
import subprocess

MAX_BYTES = 20 * 1024 * 1024
MIME_TYPES = {'audio/webm', 'audio/ogg', 'audio/mp4', 'audio/wav', 'audio/x-wav'}


class Recordings:
    def __init__(self, data):
        data.mkdir(parents=True, exist_ok=True)
        self.path = data / 'recordings.sqlite3'
        with self.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS recordings (
              edition TEXT NOT NULL, page INTEGER NOT NULL, next_version INTEGER NOT NULL DEFAULT 0,
              version INTEGER NOT NULL DEFAULT 0, mime TEXT, audio BLOB, updated_at TEXT,
              PRIMARY KEY (edition, page))''')
            if 'assessment' not in {row[1] for row in db.execute('PRAGMA table_info(recordings)')}:
                db.execute('ALTER TABLE recordings ADD COLUMN assessment TEXT')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        # Replaced user audio is removed, including free SQLite cells. No WAL history.
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, edition, page):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('''INSERT INTO recordings (edition,page,next_version) VALUES (?,?,1)
              ON CONFLICT(edition,page) DO UPDATE SET next_version=next_version+1''', (edition, page))
            return db.execute('SELECT next_version FROM recordings WHERE edition=? AND page=?',
                              (edition, page)).fetchone()[0]

    def list(self, edition):
        with self.connection() as db:
            return {str(page): {'version': version, 'updated_at': updated,
                               'assessment': json.loads(assessment) if assessment else None}
                    for page, version, updated, assessment in db.execute(
                        'SELECT page,version,updated_at,assessment FROM recordings WHERE edition=? AND audio IS NOT NULL',
                        (edition,))}

    def summaries(self, page_counts):
        """One score per saved page/edition; never read audio BLOBs for the shelf."""
        scores = {}
        with self.connection() as db:
            for edition, page, version, raw in db.execute(
                    'SELECT edition,page,version,assessment FROM recordings '
                    'WHERE audio IS NOT NULL AND assessment IS NOT NULL'):
                if not 1 <= page <= page_counts.get(edition, 0):
                    continue
                try:
                    result = json.loads(raw)
                    score = result.get('score')
                    if (result.get('version') != version or isinstance(score, bool)
                            or not isinstance(score, (int, float))
                            or not math.isfinite(score) or not 0 <= score <= 100):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                scores.setdefault(edition, []).append(score)
        return {edition: {'average': round(sum(values) / len(values), 1),
                          'scored_pages': len(values)} for edition, values in scores.items()}

    def save(self, edition, page, version, mime, audio):
        mime = mime.split(';')[0].strip().lower()
        if mime not in MIME_TYPES or not 0 < len(audio) <= MAX_BYTES:
            raise ValueError('Unsupported or oversized recording')
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'pipe', '-show_entries', 'stream=codec_type',
                                    '-of', 'json', '-i', 'pipe:0'], input=audio,
                                   capture_output=True, timeout=15)
            streams = json.loads(probe.stdout).get('streams', [])
            if probe.returncode or not streams or any(s.get('codec_type') != 'audio' for s in streams):
                raise ValueError('Recording must contain readable audio only')
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise ValueError('Could not verify recording audio') from error
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT next_version,version FROM recordings WHERE edition=? AND page=?',
                             (edition, page)).fetchone()
            if not row or not 0 < version <= row[0]:
                raise ValueError('Unknown recording take')
            if version <= row[1]:
                return False
            db.execute('''UPDATE recordings SET version=?,mime=?,audio=?,assessment=NULL,
              updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE edition=? AND page=?''',
                       (version, mime, audio, edition, page))
            return True

    def playback(self, edition, page):
        """Repair browser-container timing for playback, preserving the stored take.

        Some mobile WebM recorders write seconds of audio with hours of timestamps.
        Count decoded samples before applying the duration bound, as assessment does.
        WAV has an explicit sample count and avoids the broken container clock.
        """
        recording = self.read(edition, page)
        if not recording or recording[0] not in ('audio/webm', 'audio/ogg', 'audio/mp4'):
            return recording
        import io
        import wave
        try:
            decoded = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-protocol_whitelist', 'pipe',
                '-i', 'pipe:0', '-af', 'asetpts=N/SR/TB', '-t', '61', '-ac', '1', '-ar', '48000',
                '-f', 's16le', 'pipe:1'], input=recording[1], capture_output=True, check=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError('录音暂时无法解码，请重新录制。') from error
        output = io.BytesIO()
        with wave.open(output, 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(decoded.stdout)
        return 'audio/wav', output.getvalue()

    def read(self, edition, page):
        with self.connection() as db:
            return db.execute('SELECT mime,audio FROM recordings WHERE edition=? AND page=? AND audio IS NOT NULL',
                              (edition, page)).fetchone()

    def assessment_snapshot(self, edition, page):
        with self.connection() as db:
            return db.execute('SELECT version,audio,assessment FROM recordings WHERE edition=? AND page=? AND audio IS NOT NULL',
                              (edition, page)).fetchone()

    def save_assessment(self, edition, page, version, result):
        with self.connection() as db:
            return db.execute('UPDATE recordings SET assessment=? WHERE edition=? AND page=? AND version=? AND audio IS NOT NULL',
                              (json.dumps(result, ensure_ascii=False), edition, page, version)).rowcount == 1
