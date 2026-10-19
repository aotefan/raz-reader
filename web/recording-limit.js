// A page-local practice budget; a whole-book duration is never a page estimate.
export function pageAudioSeconds(book, number) {
  const page = book?.audio?.page_map?.[String(number)];
  if (Number.isFinite(page?.duration) && page.duration > 0) return page.duration;
  const cue = book?.audio?.whole_book?.timeline?.find(item => item.page === number);
  return cue && Number.isFinite(cue.end) && Number.isFinite(cue.start) && cue.end > cue.start
    ? cue.end - cue.start : null;
}

export function recordingLimit(text, audioSeconds) {
  const words = typeof text === 'string' ? (text.match(/[A-Za-z]+(?:['’][A-Za-z]+)?/g) || []).length : 0;
  // Room for a child's slower pace and a five-second settling pause; rounded to 5s.
  const byWords = words ? words * 1.2 + 5 : 0;
  const byAudio = Number.isFinite(audioSeconds) && audioSeconds > 0 ? audioSeconds * 2 + 5 : 0;
  return Math.min(55, Math.max(10, Math.ceil((Math.max(byWords, byAudio) || 30) / 5) * 5));
}

export function recordingCountdown(seconds, { tick, finish }) {
  const deadline = Date.now() + seconds * 1000;
  let interval, timeout, stopped = false;
  function stop() { stopped = true; clearInterval(interval); clearTimeout(timeout); }
  function update() {
    if (stopped) return;
    const left = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    tick(left, seconds);
    if (!left) { stop(); finish(); }
  }
  interval = setInterval(update, 250);
  timeout = setTimeout(update, seconds * 1000);
  update();
  return { stop };
}
