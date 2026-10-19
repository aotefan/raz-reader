import { readPageText } from '/page-translation.js';
import { recognizePage } from '/ocr-text.js';

// Transparent word hit areas over the original canvas, using real OCR boxes.
// Only the currently viewed image page is recognized, never the entire PDF.
export function ocrWords(app, win, book, active, hint) {
  let version = 0, timer, layer, observer, lastPage, busy = false, disposed = false;
  const measure = win.document.createElement('canvas').getContext('2d');
  const normalHint = '单击听原声 · 双击查词';
  function clear() { observer?.disconnect(); observer = null; layer?.remove(); layer = null; }
  function current(page, id) { return !disposed && active() && app.page === page && id === version; }
  async function prepare() {
    const page = app.page;
    if (!active() || disposed || !app.pdfDocument || (page === lastPage && (busy || layer?.isConnected))) return;
    const id = ++version; lastPage = page; busy = true; clear();
    try {
      const pdf = await app.pdfDocument.getPage(page);
      const content = await readPageText(pdf);
      if (!current(page, id)) return;
      if (content.items.some(item => /[A-Za-z]/.test(item.str || ''))) { hint(normalHint); return; }
      hint('正在准备本页点读…');
      const result = await recognizePage(book, page);
      if (!current(page, id)) return;
      const sheet = app.pdfViewer.getPageView(page - 1)?.div;
      if (!sheet?.isConnected) return;
      layer = win.document.createElement('div'); layer.className = 'raz-ocr-layer';
      layer.setAttribute('aria-label', '本页识别文字');
      const spans = [];
      for (const line of result.items) for (const word of line.words || []) {
        if (!/[A-Za-z]/.test(word.text)) continue;
        const span = win.document.createElement('span'); span.textContent = word.text;
        span.style.left = `${word.x * 100}%`; span.style.top = `${word.y * 100}%`;
        layer.append(span); spans.push({ span, word, line });
      }
      sheet.append(layer);
      function resize() {
        if (!layer) return;
        const width = layer.clientWidth, height = layer.clientHeight;
        for (const {span, word, line} of spans) {
          const size = word.height * height;
          span.dataset.lineHeight = String(line.height / 1000 * height);
          span.style.fontSize = `${size}px`; span.style.lineHeight = `${size}px`;
          measure.font = `${size}px Arial`;
          span.style.transform = `scaleX(${word.width * width / Math.max(1, measure.measureText(word.text).width)})`;
        }
      }
      observer = new win.ResizeObserver(resize); observer.observe(layer); resize();
      hint(spans.length ? normalHint : '本页未识别到可点读文字');
    } catch (error) {
      if (current(page, id)) hint('本页点读准备失败，点击页面重试');
    } finally { if (id === version) busy = false; }
  }
  function schedule() {
    if (disposed) return;
    if (app.page !== lastPage) { ++version; busy = false; clear(); hint(normalHint); }
    clearTimeout(timer); timer = setTimeout(prepare, 180);
  }
  const events = ['pagesinit', 'pagechanging', 'pagerendered'];
  for (const name of events) app.eventBus.on(name, schedule);
  // A failed local OCR request can be retried without a new toolbar control.
  const retry = () => { if (!busy && !layer && active()) schedule(); };
  win.document.addEventListener('pointerdown', retry);
  schedule();
  return () => {
    disposed = true; ++version; clearTimeout(timer); clear();
    for (const name of events) app.eventBus.off(name, schedule);
    win.document.removeEventListener('pointerdown', retry);
  };
}
