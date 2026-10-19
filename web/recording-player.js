// Native media playback with a compact, keyboard-accessible transport.
export function recordingPlayer({ beforePlay, onError }) {
  const $ = id => document.getElementById(id);
  const audio = $('recording'), player = $('recording-player');
  // Do not convert/download a saved recording while the model is scoring it.
  audio.preload = 'none';
  const button = $('recording-play');
  let source = null, duration = 0, probe;
  const format = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  function update() {
    if (Number.isFinite(audio.duration) && audio.duration > 0) duration = audio.duration;
    const elapsed = Math.min(audio.currentTime || 0, duration || Infinity);
    const playing = !audio.paused && !audio.ended;
    player.classList.toggle('playing', playing);
    button.setAttribute('aria-label', playing ? '暂停我的录音' : '播放我的录音');
    button.title = button.getAttribute('aria-label') + (duration ? ` · ${format(duration)}` : '');
    button.setAttribute('aria-pressed', String(playing));
    button.style.setProperty('--played', `${duration ? elapsed / duration * 100 : 0}%`);
  }
  // MediaRecorder WebM may omit container duration. Decode only that local
  // recording to measure it; never seek the live player to a made-up timestamp.
  async function measure() {
    if (duration || !source || probe) return;
    const url = source, controller = new AbortController(); probe = controller;
    let context;
    try {
      const response = await fetch(url, { signal: controller.signal });
      if (!response.ok) return;
      const bytes = await response.arrayBuffer();
      if (controller.signal.aborted) return;
      context = new (window.AudioContext || window.webkitAudioContext)();
      const decoded = await context.decodeAudioData(bytes);
      if (!controller.signal.aborted && source === url) { duration = decoded.duration; update(); }
    } catch { /* Playback still works when a browser cannot decode for measurement. */ }
    finally { if (context) await context.close(); }
  }
  audio.addEventListener('loadedmetadata', () => { update(); measure(); });
  for (const event of ['timeupdate', 'durationchange', 'play', 'pause', 'ended', 'emptied']) audio.addEventListener(event, update);
  audio.addEventListener('play', () => { if (player.hidden) audio.pause(); else beforePlay(); });
  audio.addEventListener('error', () => { if (source) onError('录音暂时无法播放，请重新打开本页再试。'); });
  button.onclick = async () => {
    if (!source || player.hidden) return;
    if (!audio.paused) { audio.pause(); return; }
    const requestedSource = source;
    try {
      if (audio.ended || duration && audio.currentTime >= duration) audio.currentTime = 0;
      await audio.play();
    } catch (error) {
      if (source === requestedSource && error.name !== 'AbortError') onError('录音暂时无法播放，请再点一次。');
    }
  };
  return {
    setSource(url) {
      if (source !== url) {
        probe?.abort(); probe = null; source = url; duration = 0;
        audio.pause(); audio.removeAttribute('src'); audio.load();
        if (url) audio.src = url;
      }
      player.hidden = !url;
      player.closest('.speak-actions').classList.toggle('has-recording', !!url);
      player.closest('.reader-actions').classList.toggle('has-recording', !!url);
      update();
    }
  };
}
