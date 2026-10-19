import { sentenceSpeech } from '/sentence-speech.js';
import { textGestures } from '/text-gestures.js';
// Reuse PDF.js text or local OCR word boxes with the same local dictionary UI.
export function wordPopover(beforeOpen, playOriginal) {
  const $ = id => document.getElementById(id), popup = $('word-popover');
  const audio = $('popover-audio');
  let requestId = 0, lookupAbort, highlightedDocument, speechAbort, speechUrl, selectedWord;

  let sentenceDocument;
  function clearSentenceHighlight() {
    sentenceDocument?.defaultView?.CSS?.highlights?.delete('raz-speaking');
    sentenceDocument = null;
  }
  const sentence = sentenceSpeech(beforeOpen, {
    stopped: clearSentenceHighlight,
    playing(ranges) {
      if (!ranges?.length || ranges.some(r => !r.startContainer.isConnected)) return;
      const doc = ranges[0].startContainer.ownerDocument, win = doc.defaultView;
      if (win.CSS?.highlights && win.Highlight) {
        win.CSS.highlights.set('raz-speaking', new win.Highlight(...ranges)); sentenceDocument = doc;
      }
    },
  }, playOriginal);
  let gestures;

  function stopAudio() {
    speechAbort?.abort(); speechAbort = null;
    audio.pause(); audio.removeAttribute('src'); audio.load();
    if (speechUrl) URL.revokeObjectURL(speechUrl);
    speechUrl = null;
    $('popover-pronounce').disabled = true;
    $('popover-pronounce').textContent = '发音';
    $('popover-speech-status').textContent = '';
  }
  function close() { gestures?.cancel(); closeContent(); }
  function closeContent() {
    sentence.stop();
    ++requestId; lookupAbort?.abort(); lookupAbort = null; stopAudio();
    popup.hidden = true;
    highlightedDocument?.defaultView?.CSS?.highlights?.delete('raz-lookup');
    highlightedDocument = null;
  }
  function position(anchor) {
    const margin = 12, box = popup.getBoundingClientRect();
    const x = Math.max(margin, Math.min(anchor.left, innerWidth - box.width - margin));
    let y = anchor.bottom + 10;
    if (y + box.height > innerHeight - margin) y = anchor.top - box.height - 10;
    y = Math.max(margin, Math.min(y, innerHeight - box.height - margin));
    popup.style.left = `${x}px`; popup.style.top = `${y}px`;
  }
  async function open(word, anchor, range) {
    close();
    if (beforeOpen() === false) return;
    const id = requestId;
    popup.hidden = false;
    $('popover-word').textContent = word;
    selectedWord = word;
    $('popover-phonetic').textContent = '';
    $('popover-meanings').textContent = '正在查词…';
    $('popover-pronounce').disabled = false;
    const doc = range.startContainer.ownerDocument, win = doc.defaultView;
    win.getSelection()?.removeAllRanges();
    if (win.CSS?.highlights && win.Highlight) {
      win.CSS.highlights.set('raz-lookup', new win.Highlight(range)); highlightedDocument = doc;
    }
    position(anchor);
    lookupAbort = new AbortController();
    try {
      const response = await fetch('/api/dictionary?word=' + encodeURIComponent(word), { signal: lookupAbort.signal });
      const result = await response.json();
      if (id !== requestId || popup.hidden) return;
      if (!response.ok) throw new Error(result.error || '查词暂不可用');
      if (!result.available) $('popover-meanings').textContent = '词典暂不可用。';
      else if (!result.found) $('popover-meanings').textContent = '词典未收录这个词，暂时没有释义。';
      else {
        $('popover-word').textContent = result.word;
        $('popover-phonetic').textContent = result.phonetic ? `/${result.phonetic}/` : '暂无音标';
        const meanings = document.createElement('ul');
        for (const meaning of result.meanings) {
          const item = document.createElement('li'); item.textContent = meaning; meanings.append(item);
        }
        if (!result.meanings.length) meanings.textContent = '暂无中文释义';
        $('popover-meanings').replaceChildren(meanings);
      }
      position(anchor);
    } catch (error) {
      if (id === requestId && !popup.hidden) {
        $('popover-meanings').textContent = error.message || '查词暂不可用，请重新点这个词。'; position(anchor);
      }
    }
  }
  $('popover-pronounce').onclick = async () => {
    const id = requestId, button = $('popover-pronounce');
    if (!audio.paused) { audio.pause(); return; }
    try {
      button.disabled = true; button.textContent = '准备中…';
      if (!speechUrl) {
        speechAbort = new AbortController();
        const response = await fetch('/api/pronounce', {method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({word:selectedWord}), signal:speechAbort.signal});
        if (!response.ok) throw new Error((await response.json()).error || '发音暂不可用');
        const blob = await response.blob();
        if (id !== requestId || popup.hidden) return;
        speechAbort = null; speechUrl = URL.createObjectURL(blob); audio.src = speechUrl;
      }
      await audio.play();
      if (id === requestId && !popup.hidden) {
        button.textContent = '停止'; $('popover-speech-status').textContent = '单词发音';
      }
    } catch (error) {
      if (id === requestId && error.name !== 'AbortError') {
        button.textContent = '发音'; $('popover-speech-status').textContent = error.message;
      }
    } finally { if (id === requestId && !popup.hidden) button.disabled = false; }
  };
  audio.onpause = audio.onended = () => { $('popover-pronounce').textContent = '发音'; };
  $('close-word-popover').onclick = close;
  document.addEventListener('pointerdown', event => { if (!popup.contains(event.target)) close(); });
  window.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  window.addEventListener('resize', close);
  document.addEventListener('scroll', event => { if (!popup.contains(event.target)) close(); }, true);

  return {
    close,
    bind(win, frame, enabled) {
      gestures = textGestures(win, frame, enabled, {
        close: closeContent, lookup: open,
        speak: (text, ranges) => { closeContent(); sentence.play(text, ranges); },
      });
      const bound = gestures;
      return () => { bound.destroy(); if (gestures === bound) gestures = null; };
    },
  };
}
