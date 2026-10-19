// Playback preference only; original media bytes and page cue times stay unchanged.
const KEY = 'raz:narration-speed';
const RATES = [0.75, 1, 1.25];
export function playbackSpeed(select, media, onChange) {
  let rate = 1;
  try {
    const saved = Number(localStorage.getItem(KEY));
    if (RATES.includes(saved)) rate = saved;
  } catch { /* Reading still works when browser storage is unavailable. */ }
  function apply() {
    media.defaultPlaybackRate = rate;
    media.playbackRate = rate;
    media.preservesPitch = true;
    select.value = String(rate);
    onChange(rate);
  }
  select.addEventListener('change', () => {
    const next = Number(select.value);
    if (!RATES.includes(next)) { select.value = String(rate); return; }
    rate = next;
    try { localStorage.setItem(KEY, String(rate)); } catch { /* Session preference remains usable. */ }
    apply();
  });
  // Media loads can reset playbackRate to defaultPlaybackRate.
  media.addEventListener('loadedmetadata', apply);
  media.addEventListener('play', apply);
  apply();
  return { get rate() { return rate; } };
}
