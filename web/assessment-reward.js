import confetti from '/vendor/canvas-confetti/confetti.mjs';
import { rewardTier, rewardStars, loadRewardVoices, assessmentPassScore } from '/reward-model.js';

export function assessmentReward(button) {
  const popup = document.getElementById('assessment-reward');
  const burst = confetti.create(document.getElementById('assessment-confetti'), { resize: true, disableForReducedMotion: true });
  let context, source, gain, fallbackAudio, timer, generation = 0;
  const buffers = new Map();
  async function buffer(name) {
    if (!buffers.has(name)) buffers.set(name, (async () => {
      const response = await fetch(`/assets/rewards/${name}.mp3`);
      if (!response.ok) throw new Error('Reward audio unavailable');
      return context.decodeAudioData(await response.arrayBuffer());
    })().catch(error => { buffers.delete(name); throw error; }));
    return buffers.get(name);
  }
  function stop() {
    ++generation; clearTimeout(timer); burst.reset(); popup.hidden = true;
    if (source) { source.onended = null; source.stop(); source.disconnect(); source = null; }
    if (fallbackAudio) {
      fallbackAudio.onended = null; fallbackAudio.pause(); fallbackAudio.removeAttribute('src');
      fallbackAudio.load(); fallbackAudio = null;
    }
    gain?.disconnect(); gain = null;
    popup.dataset.sound = 'stopped';
  }
  // Resume in the record/stop/evaluate user gesture, before permission or model awaits.
  function prepare() {
    try {
      context ||= new (window.AudioContext || window.webkitAudioContext)();
      context.resume().catch(() => {});
      loadRewardVoices().then(voices => {
        for (const name of new Set(Object.values(voices))) buffer(name).catch(() => {});
      });
    } catch { /* Visual feedback remains available when audio is disabled. */ }
  }
  function decorate(score) {
    const tier = rewardTier(score);
    button.removeAttribute('aria-label');
    if (!tier) return;
    const stars = document.createElement('span'); stars.className = 'assessment-stars';
    stars.setAttribute('aria-hidden', 'true'); stars.append(...rewardStars(tier[1]));
    button.prepend(stars);
    button.setAttribute('aria-label', `${score} 分，${tier[1]} 星，查看跟读反馈`);
  }
  async function celebrate(score) {
    stop(); const tier = rewardTier(score);
    if (!tier || document.hidden) return;
    const id = generation;
    document.getElementById('assessment-reward-stars').replaceChildren(...rewardStars(tier[1]));
    document.getElementById('assessment-cheer').textContent = tier[2];
    popup.setAttribute('aria-label', `${tier[2]} ${score} 分，${tier[1]} 星`);
    const rect = button.getBoundingClientRect(), width = Math.min(280, innerWidth - 24);
    popup.style.width = `${width}px`;
    popup.style.left = `${Math.max(12, Math.min(innerWidth - width - 12, rect.left + rect.width / 2 - width / 2))}px`;
    popup.style.top = `${Math.max(12, Math.min(innerHeight - 240, rect.top - 232))}px`;
    popup.hidden = false; popup.dataset.sound = 'loading';
    popup.classList.toggle('is-practice', score < assessmentPassScore);
    if (score >= assessmentPassScore && !matchMedia('(prefers-reduced-motion: reduce)').matches) burst({ particleCount: 30, spread: 85,
      startVelocity: 15, gravity: .8, origin: { x: .5, y: .5 },
      colors: ['#f6ca51', '#d9c6fc', '#fff0ac'], shapes: ['star'], ticks: 85, scalar: .9 });
    timer = setTimeout(stop, 3200);
    const voice = (await loadRewardVoices())[tier[2]];
    if (id !== generation || popup.hidden) return;
    if (!voice) { popup.dataset.sound = 'missing-voice'; return; }
    try {
      if (!context) throw new Error('Audio gesture needed');
      // Safari may interrupt the unlocked context while the microphone is active.
      if (context.state !== 'running') await context.resume();
      if (id !== generation || popup.hidden || context.state !== 'running') return;
      const decoded = await buffer(voice);
      if (id !== generation || popup.hidden) return;
      source = context.createBufferSource(); source.buffer = decoded;
      gain = context.createGain(); gain.gain.value = 1;
      source.connect(gain); gain.connect(context.destination);
      source.onended = () => { gain?.disconnect(); gain = null; source?.disconnect(); source = null; popup.dataset.sound = 'ended'; };
      source.start(); popup.dataset.sound = 'playing'; popup.dataset.duration = String(decoded.duration);
      clearTimeout(timer); timer = setTimeout(stop, Math.max(3200, (decoded.duration + .25) * 1000));
    } catch {
      if (id !== generation || popup.hidden) return;
      // Safari may interrupt Web Audio when microphone capture ends. Retry the
      // same checked local voice through the native media pipeline.
      fallbackAudio = new Audio(`/assets/rewards/${voice}.mp3`);
      fallbackAudio.preload = 'auto'; fallbackAudio.volume = 1;
      fallbackAudio.onended = () => { if (id === generation) popup.dataset.sound = 'ended'; };
      try {
        await fallbackAudio.play();
        if (id !== generation || popup.hidden) return;
        popup.dataset.sound = 'playing';
        popup.dataset.duration = String(Number.isFinite(fallbackAudio.duration) ? fallbackAudio.duration : 0);
      } catch { if (id === generation) popup.dataset.sound = 'unavailable'; }
    }
  }
  document.addEventListener('click', event => {
    if (event.target.closest('#record, #stop-record, #assess-recording')) prepare();
  }, true);
  // A new reading action takes priority, including sentence audio inside the PDF iframe.
  document.addEventListener('pointerdown', stop, true);
  document.addEventListener('keydown', stop, true);
  document.addEventListener('play', stop, true);
  document.addEventListener('visibilitychange', () => { if (document.hidden) stop(); });
  window.addEventListener('blur', stop);
  window.addEventListener('pagehide', stop);
  window.addEventListener('resize', stop);
  return { stop, decorate, celebrate, token: () => generation };
}
