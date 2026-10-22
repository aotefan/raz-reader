// A small local outbox: only book identifiers and progress, never reading media.
const PREFIX = 'little-reader:pending:';
const streams = new Map();
let browserOnly = false;
let scope = 'local';
// A reading day is shared by all devices. A device's system timezone must
// not move China's morning completions into yesterday or reset them at noon.
export const READING_TIME_ZONE = 'Asia/Shanghai';
const readingDate = new Intl.DateTimeFormat('en', {
  timeZone: READING_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit',
});
const keyFor = edition => PREFIX + (scope === 'local' ? '' : scope + ':') + edition;
export function configureProgress(options = {}) {
  browserOnly = options.browserOnly === true;
  const next = options.scope || 'local';
  if (next !== scope) streams.clear();
  scope = next;
}
function valid(body, book) {
  return body && body.edition_id === book.edition_id && Number.isInteger(body.page)
    && body.page >= 1 && body.page <= book.page_count && typeof body.completed === 'boolean'
    && typeof body.writer_id === 'string' && /^[A-Za-z0-9_-]{8,80}$/.test(body.writer_id)
    && Number.isSafeInteger(body.sequence) && body.sequence > 0;
}
function read(key) { try { return localStorage.getItem(key); } catch { return null; } }
export function pendingProgress(books) {
  return books.flatMap(book => {
    let serialized = read(keyFor(book.edition_id));
    // Migrate the old anonymous outbox only into an anonymous identity, once.
    if (!serialized && scope.startsWith('g-')) {
      const legacy = read(PREFIX + book.edition_id);
      if (legacy) try {
        serialized = JSON.stringify({ ...JSON.parse(legacy), reader_scope: scope });
        localStorage.setItem(keyFor(book.edition_id), serialized);
        localStorage.removeItem(PREFIX + book.edition_id);
      } catch { serialized = null; }
    }
    if (!serialized) return [];
    try {
      const body = JSON.parse(serialized);
      const acknowledged = book.acknowledged_writes?.[body?.writer_id] || 0;
      return valid(body, book) && (scope === 'local' || body.reader_scope === scope)
        && body.sequence > acknowledged ? [{ body, serialized, backedUp: true, scope }] : [];
    } catch { return []; }
  });
}
export function localDay(date = new Date()) {
  const parts = Object.fromEntries(readingDate.formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
export function captureProgress(book, page, complete = false) {
  let stream = streams.get(book.edition_id);
  const pending = pendingProgress([book])[0];
  if (!stream) {
    stream = { writer: pending?.body.writer_id || crypto.randomUUID(), sequence: pending?.body.sequence || 0 };
    streams.set(book.edition_id, stream);
  }
  if (pending?.body.writer_id === stream.writer) stream.sequence = Math.max(stream.sequence, pending.body.sequence);
  // Opening an already completed last page is not a new completion. Explicit
  // marking, or reaching the last page from another page, counts once per day.
  const finished = complete || (page === book.page_count && (!book.progress.completed || book.progress.page !== page));
  const days = [...new Set([...(pending?.body.completion_days || []), ...(finished ? [localDay()] : [])])];
  const body = { edition_id: book.edition_id, page,
    completed: Boolean(book.progress.completed || page === book.page_count),
    completion_days: days, writer_id: stream.writer, sequence: ++stream.sequence, read_at: new Date().toISOString(), reader_scope: scope };
  const serialized = JSON.stringify(body);
  let backedUp = false;
  try { localStorage.setItem(keyFor(book.edition_id), serialized); backedUp = true; } catch { /* Server save still works. */ }
  return { body, serialized, backedUp, scope };
}
export function isLatestProgress(snapshot) {
  if (snapshot.scope !== scope) return false;
  const stream = streams.get(snapshot.body.edition_id);
  if (stream && stream.writer === snapshot.body.writer_id && stream.sequence > snapshot.body.sequence) return false;
  if (!snapshot.backedUp) return true; // Read-only/full storage must never block a server save.
  const stored = read(keyFor(snapshot.body.edition_id));
  return !stored || stored === snapshot.serialized;
}
export async function syncProgress(snapshot) {
  if (!isLatestProgress(snapshot)) return false;
  if (browserOnly) {
    if (!snapshot.backedUp) throw new Error('Browser storage unavailable');
    return true; // Keep this device's record; the cloud serves read-only files.
  }
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: snapshot.serialized, keepalive: true, signal: controller.signal });
    if (!response.ok) throw new Error('Progress save failed');
    const result = await response.json();
    if (!result.saved && !result.stale) throw new Error('Progress save was not acknowledged');
    if (snapshot.scope !== scope) return false;
    const stored = read(keyFor(snapshot.body.edition_id));
    let canClear = stored === snapshot.serialized;
    try {
      const previous = JSON.parse(stored);
      canClear ||= previous?.writer_id === snapshot.body.writer_id && previous.sequence <= snapshot.body.sequence;
    } catch { /* Invalid old storage is not treated as an acknowledgement. */ }
    if (canClear) {
      try { localStorage.removeItem(keyFor(snapshot.body.edition_id)); } catch { /* Retry safely next load. */ }
    }
    return true;
  } finally { clearTimeout(timeout); }
}
export function flushProgress(snapshot) {
  if (browserOnly || snapshot.scope !== scope) return;
  // Send immediately, independent of the normal queue. Server sequence guards
  // prevent an earlier delayed request from overwriting this closing snapshot.
  const body = new Blob([snapshot.serialized], { type: 'application/json' });
  try { if (navigator.sendBeacon('/api/progress', body)) return; } catch { /* Keepalive fallback below. */ }
  fetch('/api/progress', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: snapshot.serialized, keepalive: true }).catch(() => {});
}
