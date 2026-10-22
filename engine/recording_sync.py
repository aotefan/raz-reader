"""Private recording replication. Transfers saved evidence; never runs a model.

Take IDs survive device-local version changes. Record time orders new takes;
assessment time orders corrections to the same take. An atomic per-page outbox
retains the original account credential and survives restarts and failed uploads.
"""
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

MAX_MESSAGE = 21_000_000


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def pack(metadata, audio=b''):
    raw = json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode()
    if len(raw) > 1024 * 1024 or len(raw) + len(audio) + 4 > MAX_MESSAGE:
        raise ValueError('录音同步内容过大。')
    return len(raw).to_bytes(4, 'big') + raw + audio


def unpack(raw):
    size = int.from_bytes(raw[:4], 'big')
    if len(raw) > MAX_MESSAGE or not 0 < size <= 1024 * 1024 or len(raw) < size + 4:
        raise ValueError('Invalid recording sync message')
    metadata = json.loads(raw[4:size + 4])
    if not isinstance(metadata, dict):
        raise ValueError('Invalid recording metadata')
    return metadata, raw[size + 4:]


def stamp(metadata):
    return metadata['take_id'] + ':' + (metadata.get('assessment_at') or '')


def timestamp(value):
    if not isinstance(value, str) or len(value) > 32:
        raise ValueError('Invalid recording timestamp')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or not datetime(2000, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError('Invalid recording timestamp')
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (TypeError, ValueError) as error:
        raise ValueError('Invalid recording timestamp') from error


class RecordingSyncStore:
    def sync_schema(self, db):
        columns = {r[1] for r in db.execute('PRAGMA table_info(recordings)')}
        for name in ('take_id', 'assessment_at', 'synced_stamp', 'reserved_at'):
            if name not in columns:
                db.execute(f'ALTER TABLE recordings ADD COLUMN {name} TEXT')
        # Give old takes stable identities once; audio and scores remain unchanged.
        for edition, page, updated, assessment in db.execute(
                'SELECT edition,page,updated_at,assessment FROM recordings WHERE audio IS NOT NULL AND take_id IS NULL').fetchall():
            db.execute('UPDATE recordings SET take_id=?,assessment_at=? WHERE edition=? AND page=?',
                       (uuid.uuid4().hex, updated if assessment else None, edition, page))
        db.execute('''CREATE TABLE IF NOT EXISTS recording_outbox (
            edition TEXT NOT NULL, page INTEGER NOT NULL, stamp TEXT NOT NULL,
            cookies TEXT NOT NULL, scope TEXT NOT NULL, blocked INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (edition,page))''')

    def queue_recording(self, db, edition, page):
        context = self.sync_context
        if not context or edition not in context['editions']:
            return
        row = db.execute('SELECT take_id,assessment_at,synced_stamp FROM recordings '
                         'WHERE edition=? AND page=? AND audio IS NOT NULL', (edition, page)).fetchone()
        if not row:
            return
        current = row[0] + ':' + (row[1] or '')
        if current != row[2]:
            db.execute('''INSERT INTO recording_outbox VALUES (?,?,?,?,?,0)
                ON CONFLICT(edition,page) DO UPDATE SET stamp=excluded.stamp,
                cookies=excluded.cookies,scope=excluded.scope,blocked=0''',
                       (edition, page, current, context['cookies'], context['scope']))

    def backfill_sync(self):
        if not self.sync_context:
            return
        with self.connection() as db:
            for edition, page in db.execute('SELECT edition,page FROM recordings WHERE audio IS NOT NULL').fetchall():
                self.queue_recording(db, edition, page)

    def sync_export(self, edition, page):
        with self.connection() as db:
            row = db.execute('SELECT take_id,updated_at,mime,audio,assessment,assessment_at '
                             'FROM recordings WHERE edition=? AND page=? AND audio IS NOT NULL', (edition, page)).fetchone()
        if not row:
            return None
        take, updated, mime, audio, assessment, assessed = row
        return {'edition_id': edition, 'page': page, 'take_id': take, 'recorded_at': updated,
                'mime': mime, 'assessment': json.loads(assessment) if assessment else None,
                'assessment_at': assessed}, audio

    def sync_import(self, metadata, audio, *, downloading=False):
        from engine.assessment import reference_text
        from engine.assessment_remote import valid_result
        edition, page = metadata['edition_id'], metadata['page']
        take = metadata.get('take_id')
        if not isinstance(take, str) or not re.fullmatch(r'[a-f0-9]{32}', take):
            raise ValueError('Invalid recording identity')
        recorded = timestamp(metadata.get('recorded_at'))
        result = metadata.get('assessment')
        assessed = timestamp(metadata.get('assessment_at')) if result is not None else None
        if result is not None:
            if not isinstance(result, dict):
                raise ValueError('Invalid saved assessment')
            clean = {k: v for k, v in result.items() if k != 'version'}
            if 'error' in clean or not valid_result(clean, reference_text(clean.get('reference'))):
                raise ValueError('Invalid saved assessment')
        mime = metadata.get('mime')
        # Validate bytes before taking the write lock. Metadata-only score updates
        # are permitted only for an identical already-stored take.
        if audio:
            self.validate_audio(mime, audio)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT take_id,updated_at,version,next_version,assessment_at,reserved_at '
                             'FROM recordings WHERE edition=? AND page=?', (edition, page)).fetchone()
            same = row and row[0] == take
            if row and row[0] and not same and (timestamp(row[1]), row[0]) >= (recorded, take):
                return {'accepted': True, 'stale': True}
            if row and row[3] > row[2] and row[5]:
                reserved = datetime.fromisoformat(row[5].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) - reserved < timedelta(minutes=3):
                    # This applies on both devices. Don't invalidate a take token
                    # while its microphone is running/uploading. Cancelled tokens
                    # expire so they cannot block later synchronization forever.
                    return {'accepted': False, 'busy': True}
            if same:
                if result is not None and (not row[4] or timestamp(row[4]) < assessed):
                    result = {**result, 'version': row[2]}
                    db.execute('UPDATE recordings SET assessment=?,assessment_at=? WHERE edition=? AND page=?',
                               (json.dumps(result, ensure_ascii=False), assessed, edition, page))
                else:
                    assessed = row[4]
            else:
                if not audio:
                    raise ValueError('Recording audio required')
                version = (row[3] if row else 0) + 1
                result = {**result, 'version': version} if result is not None else None
                db.execute('''INSERT INTO recordings
                    (edition,page,next_version,version,mime,audio,updated_at,assessment,take_id,assessment_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(edition,page) DO UPDATE SET
                    next_version=excluded.next_version,version=excluded.version,mime=excluded.mime,
                    audio=excluded.audio,updated_at=excluded.updated_at,assessment=excluded.assessment,
                    take_id=excluded.take_id,assessment_at=excluded.assessment_at''',
                    (edition, page, version, version, mime, audio, recorded,
                     json.dumps(result, ensure_ascii=False) if result is not None else None, take, assessed))
            if downloading:
                db.execute('UPDATE recordings SET synced_stamp=? WHERE edition=? AND page=?',
                           (take + ':' + (assessed or ''), edition, page))
            return {'accepted': True, 'stale': False}
