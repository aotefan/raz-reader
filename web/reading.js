// Reading records are derived from saved progress. No invented streaks or time totals.
export function readingState(book) {
  return book.progress.completed ? 'completed' : book.progress.visited ? 'reading' : 'new';
}
export function lastRead(book) {
  const value = book.progress.read_at || book.progress.updated_at;
  if (!value || typeof value !== 'string') return 0;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T') + 'Z';
  const time = Date.parse(normalized);
  return Number.isFinite(time) ? time : 0;
}
export function mostRecentFirst(a, b) {
  return lastRead(b) - lastRead(a) || a.book_id.localeCompare(b.book_id);
}
export function shelfOrder(a, b) {
  const rank = { reading: 0, new: 1, completed: 2 };
  const left = readingState(a), right = readingState(b);
  return rank[left] - rank[right] || (left === 'new' ? 0 : mostRecentFirst(a, b));
}
