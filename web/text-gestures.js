// Reuse native and OCR text hit areas. Keep upstream DOM intact.
export function sentenceTarget(range) {
  const doc = range.startContainer.ownerDocument;
  const layer = range.startContainer.parentElement.closest('.textLayer, .raz-ocr-layer');
  if (!layer) return null;
  const walker = doc.createTreeWalker(layer, doc.defaultView.NodeFilter.SHOW_TEXT);
  let node, text = '', offset = -1, previous;
  const nodes = [];
  while ((node = walker.nextNode())) {
    if (!node.textContent.trim()) continue;
    const rect = node.parentElement.getBoundingClientRect();
    if (!rect.width || !rect.height) continue;
    // OCR lowercase words can be much shorter than their line's full height.
    // Use the line box when deciding whether a wrapped sentence continues.
    const lineHeight = Math.max(rect.height, Number(node.parentElement.dataset.lineHeight) || 0);
    if (previous) text += Math.abs(rect.top - previous.top) > Math.max(lineHeight, previous.height) * 1.8 ? '\n\n' : ' ';
    if (node === range.startContainer) offset = text.length + range.startOffset;
    nodes.push({ node, start: text.length, end: text.length + node.textContent.length });
    text += node.textContent; previous = { top: rect.top, height: lineHeight };
  }
  if (offset < 0) return null;
  let base = 0;
  for (const block of text.split('\n\n')) {
    if (offset >= base && offset < base + block.length) {
      for (const part of new Intl.Segmenter('en', { granularity: 'sentence' }).segment(block)) {
        if (offset - base >= part.index && offset - base < part.index + part.segment.length) {
          const start = base + part.index, end = start + part.segment.length, ranges = [];
          for (const item of nodes) {
            const from = Math.max(start, item.start), to = Math.min(end, item.end);
            if (to <= from) continue;
            const selected = doc.createRange();
            selected.setStart(item.node, from - item.start); selected.setEnd(item.node, to - item.start);
            ranges.push(selected);
          }
          return { text: part.segment.trim(), ranges };
        }
      }
    }
    base += block.length + 2;
  }
  return null;
}
export function sentenceAt(range) { return sentenceTarget(range)?.text || ''; }

export function textGestures(win, frame, enabled, { close, lookup, speak }) {
  let down, pending;
  const abort = new AbortController(), options = { signal: abort.signal };
  function cancel() { if (pending) clearTimeout(pending.timer); pending = null; }
  function reset() { cancel(); down = null; close(); }
  win.document.addEventListener('pointerdown', event => {
    if (!event.isPrimary) { reset(); return; }
    close();
    down = event.isPrimary && event.button === 0
      ? { x: event.clientX, y: event.clientY, id: event.pointerId, at: performance.now() } : null;
  }, options);
  win.document.addEventListener('pointermove', event => {
    if (down && Math.hypot(event.clientX - down.x, event.clientY - down.y) > 6) { down = null; cancel(); }
  }, options);
  win.document.addEventListener('pointercancel', reset, options);
  win.document.addEventListener('touchstart', event => { if (event.touches.length > 1) reset(); }, { ...options, capture: true, passive: true });
  win.document.addEventListener('pointerup', event => {
    const start = down; down = null;
    if (!start || start.id !== event.pointerId || performance.now() - start.at > 700 || !enabled()) { cancel(); return; }
    const span = event.target.closest?.('.textLayer span, .raz-ocr-layer span');
    if (!span) { cancel(); return; }
    const walker = win.document.createTreeWalker(span, win.NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      for (const match of node.textContent.matchAll(/[A-Za-z]+(?:['’\-][A-Za-z]+)*/g)) {
        if (match[0].length > 60) continue;
        const range = win.document.createRange();
        range.setStart(node, match.index); range.setEnd(node, match.index + match[0].length);
        const rect = [...range.getClientRects()].find(r => event.clientX >= r.left && event.clientX <= r.right
          && event.clientY >= r.top && event.clientY <= r.bottom);
        if (!rect) continue;
        const host = frame.getBoundingClientRect();
        const anchor = { left: host.left + frame.clientLeft + rect.left,
          top: host.top + frame.clientTop + rect.top, bottom: host.top + frame.clientTop + rect.bottom };
        const twice = pending && pending.node === node && pending.offset === match.index;
        cancel();
        if (twice) { lookup(match[0], anchor, range); return; }
        pending = { node, offset: match.index, timer: setTimeout(() => {
          pending = null;
          if (enabled()) {
            const sentence = sentenceTarget(range);
            speak(sentence?.text || '', sentence?.ranges || []);
          }
        }, 400) };
        return;
      }
    }
    cancel();
  }, options);
  // Suppress browser word-selection/zoom for our double-tap, preserving drag selection.
  win.document.addEventListener('dblclick', event => {
    if (enabled() && event.target.closest?.('.textLayer span, .raz-ocr-layer span')) {
      event.preventDefault(); win.getSelection()?.removeAllRanges();
    }
  }, options);
  win.document.addEventListener('scroll', reset, { ...options, capture: true });
  win.addEventListener('keydown', event => { if (event.key === 'Escape') reset(); }, options);
  return { cancel, destroy() { abort.abort(); reset(); } };
}
