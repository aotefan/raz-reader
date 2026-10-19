// Original audio only: verified time slices or existing native PDF page media.
export function sentenceSpeech(beforePlay, { playing = () => {}, stopped = () => {} } = {}, playOriginal = () => false) {
  const $ = id => document.getElementById(id), indicator = $('sentence-player'), audio = $('sentence-audio');
  let version = 0, abort, url;
  function stop() {
    ++version; abort?.abort(); abort = null; stopped(); indicator.hidden = true;
    audio.pause(); audio.removeAttribute('src'); audio.load();
    if (url) URL.revokeObjectURL(url);
    url = null;
  }
  function status(state, text) {
    indicator.hidden = false; indicator.dataset.state = state; $('sentence-status').textContent = text;
    $('stop-sentence').setAttribute('aria-label', state === 'error' ? '关闭原声提示' : '停止句子原声');
    $('stop-sentence').title = state === 'error' ? '关闭提示' : '停止朗读';
  }
  $('stop-sentence').onclick = stop;
  audio.onended = stop;
  audio.onpause = stopped;
  audio.onerror = () => { if (audio.hasAttribute('src')) { stop(); status('error', '原声片段播放失败，请重试。'); } };
  return { stop, async play(text, ranges) {
    stop();
    if (beforePlay() === false) return;
    const id = version;
    try {
      const original = await playOriginal(text);
      if (id !== version || original === true) return;
      if (!original?.url?.startsWith('/api/sentence-audio/')) {
        status('error', '暂无本句原声定位，请用朗读播放已有原配音。'); return;
      }
      status('loading', '正在准备句子原声…');
      abort = new AbortController();
      const response = await fetch(original.url, { signal: abort.signal });
      if (!response.ok) throw new Error((await response.json()).error || '原声片段暂不可用');
      const blob = await response.blob();
      if (id !== version) return;
      abort = null; url = URL.createObjectURL(blob); audio.src = url;
      audio.playbackRate = original.rate || 1; audio.preservesPitch = true;
      status('playing', '句子原声');
      await audio.play();
      if (id === version && !audio.paused) playing(ranges);
    } catch (error) {
      if (id !== version || error.name === 'AbortError') return;
      stopped(); status('error', '原声片段暂未播放，请重试朗读。');
    }
  } };
}
