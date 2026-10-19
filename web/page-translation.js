// Translate current-page text locally, with on-demand OCR for image-only pages.
import { recognizePage } from '/ocr-text.js';
export async function readPageText(pdfPage, signal) {
  // PDF.js getTextContent uses ReadableStream async iteration, which some
  // WebKit versions lack. Consume its public stream with the portable reader API.
  if (!pdfPage.streamTextContent) return pdfPage.getTextContent();
  const reader = pdfPage.streamTextContent().getReader(), items = [];
  const cancel = () => reader.cancel().catch(() => {});
  signal?.addEventListener('abort', cancel, { once: true });
  let complete = false;
  try {
    while (true) {
      if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
      const { value, done } = await reader.read();
      if (done) { complete = true; return { items }; }
      items.push(...value.items);
    }
  } finally {
    signal?.removeEventListener('abort', cancel);
    if (!complete) await cancel();
    reader.releaseLock();
  }
}

function bodyItems(content, pageView) {
  let items = content.items.filter(item => typeof item.str === 'string' && item.str.trim());
  const fontSize = item => Math.abs(item.height || Math.hypot(item.transform?.[2] || 0, item.transform?.[3] || 0));
  const height = pageView?.[3] - pageView?.[1];
  if (content.source === 'Tesseract eng' && height > 0) {
    const bottom = item => item.transform ? (pageView[3] - item.transform[5]) / height : NaN;
    // Oversized single letters inside an illustration are not the caption.
    // Exclude them before estimating footer typography: otherwise "mM" in
    // Counting Letters makes the smaller, valid "Two letters." look like a footer.
    const captions = items.filter(item => bottom(item) >= .8 && bottom(item) <= .92
      && fontSize(item) / height >= .014 && fontSize(item) / height <= .075
      && /[A-Za-z]/.test(item.str) && /[.!?][”"']?\s*$/.test(item.str));
    if (captions.length) {
      const captionSize = Math.max(...captions.map(fontSize));
      const captionTop = Math.min(...captions.map(item => bottom(item) - fontSize(item) / height));
      items = items.filter(item => {
        const letters = item.str.match(/[A-Za-z]+/g);
        const illustrationLetters = letters?.length && letters.every(word => word.length <= 2)
          && bottom(item) > .12 && bottom(item) < captionTop
          && fontSize(item) > captionSize * 1.8;
        return !illustrationLetters;
      });
    }
  }
  const largest = Math.max(0, ...items.map(fontSize));
  return items.filter(item => {
    if (!(height > 0) || !item.transform || !fontSize(item)) return true;
    const fromTop = (pageView[3] - item.transform[5]) / height;
    const margin = fromTop < .12 || fromTop > .86;
    if (!margin) return true;
    const pageNumber = /^(?:page\s*)?\d+(?:\s*(?:of|\/)\s*\d+)?$/i.test(item.str.trim());
    if (fromTop > .92 && fontSize(item) <= largest * .85) return false;
    // Use both location and typography: retain large titles near the page edge,
    // small body text in the middle, and text without reliable geometry.
    return !pageNumber && fontSize(item) > largest * .70;
  });
}

export function pageBodyText(content, pageView) {
  return bodyItems(content, pageView).map(item => item.str).join(' ').trim();
}

// Locate the last body line, using the same footer filtering as translation.
// Coordinates remain in the source space so PDF zoom never invalidates them.
function captionAnchor(content, view, ocr = false) {
  const items = bodyItems(content, view).filter(item => item.transform && /[A-Za-z]/.test(item.str));
  if (!items.length || !view) return null;
  const item = items.reduce((last, next) => next.transform[5] < last.transform[5] ? next : last);
  const height = Math.abs(item.height || item.transform[3]);
  const words = item.words || [];
  const x = words.length ? (Math.min(...words.map(w => w.x)) + Math.max(...words.map(w => w.x + w.width))) / 2 * 1000
    : item.transform[4] + (item.width || 0) / 2;
  return { x, y: item.transform[5] - (ocr ? 0 : height * .2), view, ocr };
}

export function translationChunks(text) {
  const chunks = [];
  let chunk = '';
  for (const sentence of new Intl.Segmenter('en', { granularity: 'sentence' }).segment(text.normalize('NFKC'))) {
    for (const word of sentence.segment.split(/\s+/).filter(Boolean)) {
      if (word.length > 300) throw new Error('本页包含过长的文字片段，暂时无法翻译。');
      if (chunk.length + word.length + 1 > 300) { chunks.push(chunk); chunk = ''; }
      chunk += (chunk ? ' ' : '') + word;
    }
    if (chunk) { chunks.push(chunk); chunk = ''; }
  }
  return chunks.filter(chunk => /[A-Za-z]/.test(chunk));
}

export function pageTranslation(context, beforeOpen) {
  const $ = id => document.getElementById(id), panel = $('page-translation'), button = $('translate-page');
  const resultText = $('translation-result'), status = $('translation-status'), scroll = $('translation-scroll');
  let version = 0, abort, key, anchor = null, shortPage = true;
  const cache = new Map();
  let positionFrame, unwatch;
  function position() {
    if (panel.hidden) return;
    const { viewer, page } = context(), frame = $('pdf-frame');
    const sheet = viewer?.pdfViewer?.getPageView(page - 1)?.div;
    const host = frame.getBoundingClientRect(), body = panel.parentElement.getBoundingClientRect();
    const rect = sheet?.getBoundingClientRect();
    const left = Math.max(host.left, host.left + (rect?.left || 0));
    const right = Math.min(host.right, host.left + (rect?.right || host.width));
    const top = Math.max(host.top, host.top + (rect?.top || 0));
    const bottom = Math.min(host.bottom, host.top + (rect?.bottom || host.height));
    if (!rect || right - left < 48 || bottom - top < 80) return;
    const inset = right - left < 340 ? 10 : 18;
    const caption = shortPage && resultText.textContent.length <= 100 && panel.dataset.state !== 'error';
    panel.dataset.presentation = caption ? 'caption' : 'passage';
    panel.style.maxWidth = `${Math.max(0, right - left - inset * 2)}px`;
    panel.style.top = 'auto';
    if (caption) {
      panel.style.width = 'max-content';
      panel.style.maxHeight = `${Math.max(80, (bottom - top) * .46)}px`;
      const size = panel.getBoundingClientRect();
      let center = (left + right) / 2, y = bottom - inset - size.height;
      if (anchor) {
        const pageView = viewer.pdfViewer.getPageView(page - 1);
        const point = anchor.ocr
          ? [anchor.x / 1000 * rect.width, (1 - anchor.y / 1000) * rect.height]
          : pageView.viewport.convertToViewportPoint(anchor.x, anchor.y);
        center = host.left + rect.left + point[0];
        const below = host.top + rect.top + point[1] + 6;
        // Prefer the line below the English; keep the caption visible at the
        // page edge or while zoomed. No extra menu or user positioning step.
        y = Math.max(top + inset, Math.min(below, bottom - inset - size.height));
      }
      panel.style.left = `${Math.max(left + inset, Math.min(center - size.width / 2, right - inset - size.width)) - body.left}px`;
      panel.style.bottom = 'auto';
      panel.style.top = `${y - body.top}px`;
      panel.style.visibility = 'visible';
      return;
    }
    const width = Math.max(0, Math.min(460, right - left - inset * 2));
    panel.style.width = `${width}px`;
    panel.style.left = `${(left + right - width) / 2 - body.left}px`;
    panel.style.bottom = `${body.bottom - bottom + inset}px`;
    panel.style.maxHeight = `${Math.max(80, (bottom - top) * .46)}px`;
    panel.style.visibility = 'visible';
  }
  function schedulePosition() {
    cancelAnimationFrame(positionFrame); positionFrame = requestAnimationFrame(position);
  }
  function watchPosition() {
    unwatch?.();
    const frame = $('pdf-frame'), win = frame.contentWindow, bus = context().viewer?.eventBus;
    const observer = new ResizeObserver(schedulePosition); observer.observe(frame);
    win?.addEventListener('scroll', schedulePosition, true);
    window.addEventListener('resize', schedulePosition);
    const events = ['pagerendered', 'scalechanging', 'pagechanging'];
    for (const event of events) bus?.on(event, schedulePosition);
    unwatch = () => {
      observer.disconnect(); win?.removeEventListener('scroll', schedulePosition, true);
      window.removeEventListener('resize', schedulePosition);
      for (const event of events) bus?.off(event, schedulePosition);
    };
    schedulePosition();
  }
  function setStatus(text, state) {
    status.textContent = text; status.hidden = !text; panel.dataset.state = state;
    panel.setAttribute('aria-busy', String(state === 'loading'));
    schedulePosition();
  }
  function showParagraphs(output) {
    resultText.replaceChildren(...output.map(text => {
      const paragraph = document.createElement('p'); paragraph.textContent = text; return paragraph;
    }));
  }
  function cancel() { ++version; abort?.abort(); abort = null; }
  function reset() {
    cancel(); unwatch?.(); unwatch = null; cancelAnimationFrame(positionFrame);
    key = null; anchor = null; shortPage = true; panel.hidden = true; button.setAttribute('aria-expanded', 'false');
    resultText.replaceChildren(); setStatus('', 'idle'); $('retry-translation').hidden = true;
  }
  async function render() {
    cancel();
    const { book, page, viewer, ready } = context(), id = version;
    if (!book || !ready || panel.hidden) return;
    const pageKey = `${book.edition_id}:${page}`;
    const retry = key === pageKey && panel.dataset.state === 'error';
    key = pageKey;
    panel.setAttribute('aria-label', `第 ${page} 页译文`);
    if (!retry) { resultText.replaceChildren(); scroll.scrollTop = 0; anchor = null; shortPage = true; }
    setStatus('正在翻译…', 'loading'); $('retry-translation').hidden = true; schedulePosition();
    if (!viewer?.pdfDocument) { setStatus('本页没有可用文字，暂时无法自动翻译。', 'empty'); return; }
    if (cache.has(pageKey)) {
      const saved = cache.get(pageKey); anchor = saved.anchor; shortPage = saved.shortPage;
      showParagraphs(saved.output); setStatus('', 'ready'); return;
    }
    abort = new AbortController(); const signal = abort.signal;
    try {
      const pdfPage = await viewer.pdfDocument.getPage(page);
      if (id !== version) return;
      const content = await readPageText(pdfPage, signal);
      if (id !== version) return;
      let text = pageBodyText(content, pdfPage.view);
      anchor = captionAnchor(content, pdfPage.view);
      if (!/[A-Za-z]/.test(text)) {
        setStatus('正在识别本页文字…', 'loading');
        const recognized = await recognizePage(book, page);
        if (id !== version) return;
        text = pageBodyText(recognized, recognized.view);
        anchor = captionAnchor(recognized, recognized.view, true);
      }
      shortPage = text.length <= 160;
      const chunks = translationChunks(text);
      schedulePosition();
      if (!chunks.length) { setStatus('本页没有可用的英文文字，暂时无法自动翻译。', 'empty'); return; }
      const output = [];
      for (const chunk of chunks) {
        if (id !== version) return;
        let result;
        for (let attempt = 0; attempt < 5; attempt++) {
          const response = await fetch('/api/translate', { method: 'POST', signal,
            headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: chunk }) });
          result = await response.json();
          if (id !== version) return;
          if (response.ok) break;
          // A previous page may still be finishing its local inference.
          if (response.status === 503 && result.error?.includes('正在翻译') && attempt < 4) {
            await new Promise(resolve => setTimeout(resolve, 700)); continue;
          }
          throw new Error(result.error || '翻译暂不可用');
        }
        output.push(result.translation);
        // Keep completed paragraphs stable as the rest arrives; a later error
        // must not erase the translation the reader has already started reading.
        const existing = resultText.children[output.length - 1];
        if (existing) existing.textContent = result.translation;
        else {
          const paragraph = document.createElement('p'); paragraph.textContent = result.translation; resultText.append(paragraph);
        }
        setStatus(output.length < chunks.length ? '正在翻译…' : '', output.length < chunks.length ? 'loading' : 'ready');
      }
      while (resultText.children.length > output.length) resultText.lastChild.remove();
      cache.set(pageKey, { output, anchor, shortPage });
    } catch (error) {
      if (id !== version || signal.aborted) return;
      setStatus(error.message || '翻译暂不可用', 'error'); $('retry-translation').hidden = false;
    }
  }
  button.onclick = event => {
    if (event.detail > 1) return; // A double click must not immediately dismiss the panel.
    if (!panel.hidden) { reset(); return; }
    beforeOpen(); panel.style.visibility = 'hidden'; panel.hidden = false; watchPosition();
    button.setAttribute('aria-expanded', 'true'); render();
  };
  $('close-translation').onclick = reset;
  $('retry-translation').onclick = render;
  return { reset, refresh(changed) {
    // Translation is an explicit action for one page, never a persistent reading mode.
    if (changed) reset();
    else if (!panel.hidden && !key) render();
  } };
}
