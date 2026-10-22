import * as pdfjs from '/vendor/pdfjs/build/pdf.mjs';
const SHELF_TITLE = document.title;
import { captureProgress, pendingProgress, syncProgress, flushProgress, isLatestProgress, configureProgress, localDay } from '/progress.js';
import { readingState, mostRecentFirst, shelfOrder } from '/reading.js';
import { setIdentity, scopeHeaders } from '/identity.js';
import { personalCenter } from '/personal-center.js';
import { selectionSheet } from '/selection-sheet.js';
import { NativeAudio } from '/native-audio.js';
import { pageTranslation } from '/page-translation.js';
import { wordPopover } from '/word-popover.js';
import { ocrWords } from '/ocr-words.js';
import { recordingPlayer } from '/recording-player.js';
import { recordingAssessment, assessmentReference } from '/recording-assessment.js';
import { pageAudioSeconds, recordingLimit, recordingCountdown } from '/recording-limit.js';
import { playbackSpeed } from '/playback-speed.js';
import { rewardTier, rewardStars } from '/reward-model.js';
pdfjs.GlobalWorkerOptions.workerSrc = '/vendor/pdfjs/build/pdf.worker.mjs';
const $ = id => document.getElementById(id);
const picker = selectionSheet();
const cloudReading = () => library?.mode?.startsWith('cloud_');
const readingOnly = () => library?.mode === 'cloud_reading';
let library, selected = 'all', current, viewer, page = 1, ready = false;
let coverGeneration = 0, renderGeneration = 0, saveQueue = Promise.resolve(), saveSequence = 0;
let pendingAudioAdvance = false, recorder, micStream, recordPending = false, recordRequestId = 0;
const pageRecordings = new Map(), recordingLoads = new Map(), recordingFinishing = new Map();
let coverQueue = Promise.resolve(), audioMode = 'pages', activeMediaKey = '', libraryGeneration = 0;
let readingFilter = 'all', nativeAudio, unbindWordLookup, coverObserver;
let readerTimer, readerAttempts = 0;
let selectedScope, preferenceQueue = Promise.resolve();
let renderedReadingDay;
const narration = $('narration');
const readingSpeed = playbackSpeed($('reading-speed'), narration, rate => { nativeAudio?.setRate(rate); $('sentence-audio').playbackRate = rate; });
const recordingTransport = recordingPlayer({
  beforePlay: () => { wordLookup.close(); narration.pause(); nativeAudio?.pause(); },
  onError: text => message(text),
});
const assessment = recordingAssessment(() => ({ book: current, number: page,
  clip: current && pageRecordings.get(recordingKey(current, page)), document: viewer?.pdfDocument }), refreshRecording);
const center = personalCenter({ openBook, getLevel: () => selected, beforeAuth: async (action, body) => {
  await saveQueue;
  if ([...pageRecordings.values()].some(clip => clip.blob && clip.status !== 'saved')) {
    throw new Error('还有录音未保存，请回到对应绘本完成保存后再切换账号。');
  }
  if (action === 'register' && body.import_progress && pendingProgress(library.books).length) {
    throw new Error('还有进度未同步，请联网保存后再带入账号。');
  }
} });
function rememberLevel(level) {
  const scope = library.reader_scope || 'local', key = 'little-reader:level:' + scope;
  try { localStorage.setItem(key, level); } catch { /* Server preference still works. */ }
  if (!library.account) return;
  try { localStorage.setItem(key + ':pending', level); } catch { /* Server preference still works. */ }
  preferenceQueue = preferenceQueue.catch(() => {}).then(async () => {
    const response = await fetch('/api/account/preferences', { method: 'POST', keepalive: true,
      headers: { 'Content-Type': 'application/json', ...scopeHeaders(scope) }, body: JSON.stringify({ level }) });
    if (!response.ok) throw new Error('等级暂存在此设备，联网后会重试同步。');
    try { if (localStorage.getItem(key + ':pending') === level) localStorage.removeItem(key + ':pending'); } catch { /* Optional outbox. */ }
  }).catch(error => message(error.message));
}
function message(text) { $('message').textContent = text; $('message').hidden = false; clearTimeout(message.timer); message.timer = setTimeout(() => $('message').hidden = true, 6000); }
async function loadLibrary() {
  const generation = ++libraryGeneration;
  const response = await fetch('/api/library');
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || '书库加载失败');
  const result = await response.json();
  if (generation !== libraryGeneration || current) return;
  if (library && result.reader_scope !== library.reader_scope) { location.reload(); return; }
  const browserOnly = result.mode === 'cloud_reading';
  setIdentity(result.reader_scope);
  configureProgress({ browserOnly, scope: result.reader_scope });
  for (const book of result.books) book.reader_scope = result.reader_scope || 'local';
  document.body.classList.toggle('cloud-reading', browserOnly);
  if (result.mode?.startsWith('cloud_')) {
    document.querySelector('.reading-stats small').textContent = '此浏览器的阅读记录';
    $('popover-pronounce').title = '英文单词发音';
  }
  const pending = pendingProgress(result.books);
  for (const snapshot of pending) {
    for (const book of result.books.filter(b => b.edition_id === snapshot.body.edition_id)) {
      book.completion_days = [...new Set([...(book.completion_days || []), ...(snapshot.body.completion_days || [])])];
      book.progress = { ...book.progress, page: snapshot.body.page, read_at: snapshot.body.read_at,
        completed: book.progress.completed || snapshot.body.completed, visited: true, pending_sync: !browserOnly };
    }
  }
  library = result;
  if (selectedScope !== (result.reader_scope || 'local')) {
    selectedScope = result.reader_scope || 'local';
    const key = 'little-reader:level:' + selectedScope;
    let stored, pending;
    try { stored = localStorage.getItem(key); pending = localStorage.getItem(key + ':pending'); } catch { /* Use account preference. */ }
    const preferred = pending || result.account?.level || stored || 'all';
    selected = preferred === 'all' || result.levels.some(l => l.exists && l.level === preferred) ? preferred : 'all';
    if (pending && result.account) rememberLevel(selected);
  }
  renderLevels(); renderReadingRecords(); renderShelf();
  renderAudioOverview();
  for (const snapshot of pending) {
    saveQueue = saveQueue.catch(() => {}).then(async () => {
      const synced = await syncProgress(snapshot);
      if (synced && generation === libraryGeneration && !current) {
        for (const book of library.books.filter(b => b.edition_id === snapshot.body.edition_id
          && b.progress.page === snapshot.body.page && b.progress.read_at === snapshot.body.read_at)) {
          book.progress.pending_sync = false;
        }
        renderReadingRecords();
      }
    }).catch(() => {});
  }
}
function renderLevels() {
  const existing = library.levels.filter(l => l.exists), prepared = existing.filter(l => l.prepared);
  $('level-progress').textContent = library.scope ? `${cloudReading() ? '已上传' : '已整理'} ${prepared.length} 级 · ${cloudReading() ? '待上传' : '待整理'} ${existing.length - prepared.length} 级` : '';
  const options = [];
  for (const level of [{ level: 'all', exists: true }, ...library.levels]) {
    const count = library.books.filter(b => b.level === level.level && !b.demo).length;
    const pending = Boolean(library.scope && level.exists && level.level !== 'all' && !level.prepared);
    options.push({ value: level.level, label: level.level === 'all' ? '全部等级' : level.level,
      disabled: !level.exists, detail: level.level === 'all' ? '' : !level.exists ? '暂无绘本' : `${pending ? level.discovered_count : count} 本`,
      group: level.level === 'all' ? '' : !level.exists ? '暂无绘本' : pending ? (cloudReading() ? '待上传' : '待整理') : '可阅读' });
  }
  picker.configure($('level-select'), { title: '阅读等级', value: selected, options,
    choose: value => { selected = value; rememberLevel(selected); renderLevels(); renderShelf(); } });
}
function renderShelf() {
  if (!library) return; // Search may be typed while the larger library is loading.
  const generation = ++coverGeneration;
  coverObserver?.disconnect();
  const search = $('search').value.trim().toLowerCase();
  const books = library.books.filter(b => (selected === 'all' || selected === b.level)
    && b.title.toLowerCase().includes(search) && (!$('audio-only').checked || canNarrate(b))
    && (readingFilter === 'all' || readingState(b) === readingFilter));
  books.sort(shelfOrder);
  const real = books.filter(b => !b.demo).length, demos = books.filter(b => b.demo).length;
  $('level-title').firstChild.textContent = selected === 'all' ? '全部绘本 ' : `Level ${selected} `;
  $('book-count').textContent = `${real} 本绘本${demos ? ` + ${demos} 本自制演示` : ''}`;
  const level = library.levels.find(l => l.level === selected);
  const pendingLevel = level && library.scope && !level.prepared;
  $('level-description').textContent = level ? `${pendingLevel ? `待整理 · 本地保留 ${level.discovered_count} 个 PDF · ` : ''}已导入 ${library.books.filter(b => b.level === selected && !b.demo).length} 本绘本` : `${library.levels.filter(l => l.exists).length} 个本地等级 · ${library.scope ? `${library.scope} 优先整理 · ` : ''}${library.books.filter(b => !b.demo).length} 本已导入`;
  if (cloudReading()) $('level-description').textContent = level
    ? (pendingLevel ? `尚未上传 · 本地保留 ${level.discovered_count} 本` : `云端可读 ${library.books.filter(b => b.level === selected).length} 本绘本`)
    : `AA–H · ${library.books.length} 本云端绘本${library.account ? '' : ' · 游客阅读'}`;
  $('books').replaceChildren(); $('empty').hidden = books.length > 0;
  $('empty').textContent = $('audio-only').checked ? '当前筛选下没有可朗读的绘本。可以取消筛选继续阅读。'
    : search ? '没有找到这本书，试试其他书名。'
    : readingFilter !== 'all' ? '当前等级还没有这个阅读状态的绘本。可以切换等级或选择全部状态。'
    : pendingLevel ? `本地已保留 ${level.discovered_count} 个 PDF，${cloudReading() ? '尚未上传到云端' : '尚未整理接入'}。` : '这个等级还没有导入样本。先从有绘本的等级开始吧。';
  const covers = new Map();
  for (const b of books) {
    const card = document.createElement('button'); card.className = 'book-card'; card.dataset.bookId = b.book_id;
    card.setAttribute('aria-label', `打开 ${b.title}${b.demo ? '，自制测试绘本' : ''}`);
    const wrap = document.createElement('div'); wrap.className = 'cover-wrap';
    const loading = document.createElement('span'); loading.className = 'cover-loading'; loading.textContent = b.validation === 'failed' ? '文件待修复' : '正在读取封面…';
    const canvas = document.createElement('canvas'); canvas.setAttribute('aria-label', b.title + ' 封面');
    const badge = document.createElement('span'); badge.className = 'level-badge'; badge.textContent = b.demo ? '自制测试 · AA' : `LEVEL ${b.level}`;
    wrap.append(loading, canvas, badge);
    const info = document.createElement('div'); info.className = 'book-info';
    const title = document.createElement('h3'); title.textContent = b.title; title.title = b.title;
    if (!b.demo) { const candidate = document.createElement('span'); candidate.className = 'candidate'; candidate.textContent = '待核名'; title.append(candidate); }
    const meta = document.createElement('div'); meta.className = 'book-meta';
    const progress = document.createElement('span'); progress.textContent = b.progress.completed ? '✓ 已读完' : b.progress.visited ? `读到 ${b.progress.page} / ${b.page_count} 页` : `${b.page_count} 页 · 等你来读`;
    progress.className = 'book-reading-status';
    if (b.progress.completed) {
      const summary = b.assessment_summary, tier = rewardTier(summary?.average);
      const rating = document.createElement('span'); rating.className = 'book-rating';
      if (tier && summary.scored_pages > 0) {
        const stars = document.createElement('span'); stars.className = 'book-stars';
        stars.setAttribute('aria-hidden', 'true'); stars.append(...rewardStars(tier[1]));
        const label = document.createElement('span'); label.textContent = `平均 ${summary.average} 分`;
        rating.append(stars, label);
        rating.title = `已评分 ${summary.scored_pages} 页 · 每页只取最新录音评分`;
        rating.setAttribute('aria-label', `平均 ${summary.average} 分，${tier[1]} 星，已评分 ${summary.scored_pages} 页`);
      } else { rating.classList.add('unscored'); rating.textContent = '尚未评分'; }
      progress.append(rating);
    }
    const audio = document.createElement('span'); audio.className = canNarrate(b) ? 'audio-tag' : '';
    audio.textContent = audioSummary(b);
    meta.append(progress, audio);
    const track = document.createElement('div'); track.className = 'progress-track';
    const fill = document.createElement('div'); fill.className = 'progress-fill';
    fill.style.width = `${b.progress.completed ? 100 : b.progress.visited ? b.progress.page / b.page_count * 100 : 0}%`;
    track.append(fill); info.append(title, meta, track); card.append(wrap, info);
    card.disabled = b.validation === 'failed'; card.onclick = () => openBook(b); $('books').append(card);
    if (b.validation !== 'failed') covers.set(card, { b, canvas, loading });
  }
  // At most one cover worker at a time; thumbnails remain transient canvases.
  function enqueueCover({ b, canvas, loading }) {
    coverQueue = coverQueue.catch(() => {}).then(async () => {
      if (generation !== coverGeneration) return;
      let doc, task;
      try {
        if (b.cover_url) {
          const thumbnail = new Image();
          thumbnail.src = b.cover_url;
          await thumbnail.decode();
          if (generation !== coverGeneration) return;
          canvas.width = thumbnail.naturalWidth; canvas.height = thumbnail.naturalHeight;
          canvas.getContext('2d').drawImage(thumbnail, 0, 0);
          loading.hidden = true;
          return;
        }
        task = pdfjs.getDocument({ url: b.pdf_url, isEvalSupported: false, enableXfa: false,
          cMapUrl: '/vendor/pdfjs/web/cmaps/', cMapPacked: true,
          standardFontDataUrl: '/vendor/pdfjs/web/standard_fonts/', wasmUrl: '/vendor/pdfjs/web/wasm/' });
        doc = await task.promise;
        if (generation !== coverGeneration) return;
        const first = await doc.getPage(1), base = first.getViewport({ scale: 1 });
        const viewport = first.getViewport({ scale: Math.min(400 / base.height, 350 / base.width) });
        canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
        await first.render({ canvasContext: canvas.getContext('2d'), viewport, annotationMode: 0 }).promise;
        loading.hidden = true;
      } catch (e) { if (generation === coverGeneration) { loading.textContent = '封面读取失败，仍可尝试打开'; console.error('cover', b.book_id, e); } }
      finally { if (task) await task.destroy(); }
    });
  }
  coverObserver = new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting && covers.has(entry.target)) {
      coverObserver?.unobserve(entry.target);
      enqueueCover(covers.get(entry.target)); covers.delete(entry.target);
    }
  }, { rootMargin: '300px' });
  for (const card of covers.keys()) coverObserver.observe(card);
}
function renderReadingRecords() {
  const originals = library.books.filter(b => !b.demo);
  for (const state of ['new', 'reading', 'completed']) {
    $('stat-' + state).textContent = String(originals.filter(b => readingState(b) === state).length);
  }
  renderedReadingDay = localDay();
  const today = originals.filter(b => b.completion_days?.includes(renderedReadingDay)).length;
  $('shelf-read-count').textContent = `今日读完 ${today} 本 · 累计 ${$('stat-completed').textContent} 本`;
  $('shelf-read-count').title = '按北京时间统计，每天零点更新';
  $('shelf-read-count').hidden = false;
  const recent = library.books.filter(b => readingState(b) === 'reading' && b.validation !== 'failed')
    .sort(mostRecentFirst)[0];
  $('continue-panel').hidden = !recent;
  if (recent) {
    $('continue-title').textContent = recent.title;
    $('continue-level').textContent = recent.demo ? `自制演示 · Level ${recent.level}` : `Level ${recent.level} · 书名候选`;
    $('continue-page').textContent = `上次读到第 ${recent.progress.page} / ${recent.page_count} 页${recent.progress.pending_sync ? ' · 进度暂存在此浏览器' : ''}`;
    $('continue-reading').onclick = () => openBook(recent);
  }
  picker.configure($('reading-filter'), { title: '阅读状态', value: readingFilter, layout: 'list',
    options: [{ value: 'all', label: '全部状态' }, { value: 'new', label: '未开始' },
      { value: 'reading', label: '正在读' }, { value: 'completed', label: '已读完' }],
    choose: value => { readingFilter = value; renderReadingRecords(); renderShelf(); } });
  center.update(library);
}
function canNarrate(book) {
  if (book.audio.embedded?.pages.length) return true;
  return book.audio.status.startsWith('verified_')
    && (Object.keys(book.audio.page_map).length > 0 || Boolean(book.audio.whole_book));
}
function audioSummary(book) {
  if (book.audio.embedded?.pages.length) return '♫ PDF 内嵌音频 · 可点读';
  if (book.audio.content_evidence?.result === 'automated_asr_content_match') return '♫ 原声核对通过 · 整本';
  if (canNarrate(book)) return book.demo ? '♫ 自制朗读演示'
    : Object.keys(book.audio.page_map).length ? '♫ 已核验页音频' : '♫ 已核验整本音频';
  if (book.audio.status === 'verification_failed') return '音频核验失败';
  if (book.audio.candidate_state === 'content_mismatch_or_uncertain') return '找到配音 · 版本不匹配';
  if (book.audio.candidate_state === 'insufficient_text') return '找到配音 · 内容待人工核对';
  if (book.audio.candidate_state === 'filename_candidate_unverified') return '找到配音 · 核验中';
  if (book.audio.candidate_state === 'no_independent_candidate') return '缺少独立配音';
  if (book.audio.embedded?.rich_media_pages?.length) return 'PDF 内含配音 · 暂不可播放';
  return book.audio_detection === 'detected_unverified_restricted' ? '内嵌音频待核验' : '暂无可用音频';
}
function renderAudioOverview() {
  const originals = library.books.filter(b => !b.demo), demos = library.books.filter(b => b.demo && canNarrate(b));
  const richOnly = originals.filter(b => !canNarrate(b) && b.audio.embedded?.rich_media_pages?.length).length;
  $('audio-overview-summary').textContent = `已导入 ${originals.length} 本 RAZ 绘本，其中 ${originals.filter(canNarrate).length} 本有可用朗读${richOnly ? `；${richOnly} 本检测到 PDF 内嵌音频，但当前阅读器无法播放` : ''}${demos.length ? `；另有 ${demos.length} 本自制朗读演示` : ''}。`;
  $('audio-overview-books').replaceChildren();
  for (const book of library.books) {
    const detail = book.audio.embedded?.pages.length ? '通过 PDF.js 原生按钮播放，按原 PDF 注释定位页面；未人工核对全文。'
      : canNarrate(book) ? (book.demo ? '自制测试绘本，使用本机合成语音；RAZ 原始配音尚未接入。'
        : book.audio.content_evidence?.result === 'automated_asr_content_match'
          ? '原 MP3 完整解码成功；本机转写与 PDF 文字/OCR 顺序比对通过。没有人工听校或页码/句子时间线核验。'
          : '配音已核验，可在阅读时播放。')
      : book.audio.status === 'verification_failed' ? '已禁用这份配音，可以继续阅读绘本。'
      : book.audio.candidate_state === 'content_mismatch_or_uncertain' ? '找到同名配音，但本机转写与当前 PDF 正文没有达到匹配标准；为避免播错版本已禁用。请提供这本书的正确独立原声。'
      : book.audio.candidate_state === 'insufficient_text' ? '找到同名配音，但 PDF 正文无法可靠读取，自动比对不足；未开放播放，需人工确认书本版本。'
      : book.audio.candidate_state === 'filename_candidate_unverified' ? '找到同级书名候选配音，正在与正文核对；完成前不会播放。'
      : book.audio.candidate_state === 'no_independent_candidate' ? '未找到同级独立配音；PDF 内若有受限音频则无法接入。可下载这本书的独立原声 MP3 后再关联。'
      : book.audio.embedded?.rich_media_pages?.length ? 'PDF 内检测到逐页音频结构，但文件带有复制限制，无法直接接入播放器；未导出或绕过保护。若有独立原声 MP3，核对后可关联。'
      : book.audio_detection === 'detected_unverified_restricted' ? 'PDF 检测到内嵌音频结构，使用权限、内容和完整性仍待核验。'
      : '当前尚无经过核验的配音，可以继续阅读绘本。';
    const row = document.createElement('tr');
    for (const text of [book.title, book.demo ? `${book.level} · 自制` : book.level, audioSummary(book), detail]) {
      const cell = document.createElement('td'); cell.textContent = text; row.append(cell);
    }
    $('audio-overview-books').append(row);
  }
}
const translation = pageTranslation(() => ({ book: current, page, viewer, ready }), () => {
  wordLookup.close(); narration.pause(); nativeAudio?.pause(); $('recording').pause();
});
const wordLookup = wordPopover(() => {
  if (recordPending || recorder?.state === 'recording') {
    message('请先停止录音，再点读或查词。'); return false;
  }
  narration.pause(); nativeAudio?.pause(); $('recording').pause();
}, async text => {
  synchronizePage();
  if (!ready) return false;
  const normalize = value => (value.toLowerCase().match(/[a-z0-9]+/g) || []).join(' ');
  const sentence = current.audio.sentences?.find(s => s.page === page && normalize(s.text) === normalize(text));
  if (sentence) return { url: sentence.url, rate: readingSpeed.rate };
  if (audioAsset()?.origin === 'pdf_embedded') {
    // The PDF supplies page association, not exact sentence boundaries.
    narration.currentTime = 0; await narration.play(); return true;
  }
  if (!audioAsset()?.native || nativeAudio?.records().length !== 1) return false;
  // A native page clip remains explicitly labelled as page audio.
  await nativeAudio.play(0, true);
  return true;
});
$('show-audio-overview').onclick = () => $('audio-overview').showModal();
$('close-audio-overview').onclick = () => $('audio-overview').close();
function cancelReaderLoad() {
  clearTimeout(readerTimer);
  $('retry-reader').hidden = true;
}
function readerFailed() {
  if (!current || ready) return;
  clearTimeout(readerTimer);
  setReadingControls(false);
  $('page-number').textContent = '打开失败，请重试';
  $('retry-reader').hidden = false;
  document.title = 'RAZ Reader · 打开失败';
}
function loadPDFReader() {
  cancelReaderLoad();
  const generation = renderGeneration;
  $('page-number').textContent = readerAttempts ? '正在重新打开…' : '正在打开…';
  document.title = 'RAZ Reader · 正在打开';
  $('pdf-frame').src = '/vendor/pdfjs/web/viewer.html?file=' + encodeURIComponent(current.pdf_url)
    + '&session=' + generation + '-' + readerAttempts;
  readerTimer = setTimeout(() => {
    if (generation !== renderGeneration || !current || ready) return;
    if (readerAttempts === 0) {
      // Replace the stalled frame once; callbacks from it cannot bind to the retry.
      ++readerAttempts; ++renderGeneration;
      unbindWordLookup?.(); unbindWordLookup = null;
      nativeAudio?.destroy(); nativeAudio = null; viewer = null;
      loadPDFReader();
    } else readerFailed();
  }, cloudReading() ? 60000 : 15000);
}
$('retry-reader').onclick = () => { if (current) openBook(current); };
window.addEventListener('message', event => {
  if (event.origin === location.origin && event.source === $('pdf-frame').contentWindow
      && event.data?.type === 'raz-reader-startup-error'
      && event.data.url === $('pdf-frame').src) readerFailed();
});
document.addEventListener('webviewerloaded', event => {
  const win = event.detail.source;
  if (win !== $('pdf-frame').contentWindow || !current
      || win.location.href !== $('pdf-frame').src
      || new URL(win.location.href).searchParams.get('file') !== current.pdf_url) return;
  win.addEventListener('keydown', readerShortcut, true);
  win.addEventListener('keyup', preventSpaceClick, true);
  const options = win.PDFViewerApplicationOptions;
  options.setAll({ enablePermissions: false, enableScripting: false, enableXfa: false,
    annotationEditorMode: -1, annotationMode: 1, viewOnLoad: 1,
    supportsPinchToZoom: true, defaultZoomValue: 'page-fit', scrollModeOnLoad: 3, spreadModeOnLoad: 0,
    disableAutoFetch: cloudReading(), disableStream: cloudReading(),
    isEvalSupported: false, disablePreferences: true });
  const app = win.PDFViewerApplication;
  viewer = app;
  const generation = renderGeneration;
  unbindWordLookup?.();
  const active = () => generation === renderGeneration && ready && current?.text_lookup_allowed;
  const unbindGestures = wordLookup.bind(win, $('pdf-frame'), () => generation === renderGeneration
    && ready && current?.text_lookup_allowed);
  let unbindOCR;
  // RichMedia PDFs may also render a native widget. Route that same page icon
  // through the verified MP3 transport so there is only one playback state.
  const redirectEmbedded = event => {
    if (generation !== renderGeneration || !ready || !event.target.closest?.('.mediaAnnotation')
        || current?.audio.page_map[String(app.page)]?.origin !== 'pdf_embedded') return;
    event.preventDefault(); event.stopImmediatePropagation();
    synchronizePage(); narration.paused ? playAudio() : narration.pause();
  };
  win.document.addEventListener('click', redirectEmbedded, true);
  unbindWordLookup = () => { unbindGestures(); unbindOCR?.(); win.document.removeEventListener('click', redirectEmbedded, true); };
  win.document.documentElement.classList.toggle('raz-word-lookup', Boolean(current.text_lookup_allowed));
  app.initializedPromise.then(() => {
    if (generation !== renderGeneration || !current) return;
    unbindOCR = ocrWords(app, win, current, active, text => { if (active()) $('word-lookup-hint').textContent = text; });
    nativeAudio = new NativeAudio(app, event => {
      if (generation !== renderGeneration || !current) return;
      if (event === 'play') {
        wordLookup.close(); narration.pause(); $('recording').pause();
        audioMode = 'pages'; activeMediaKey = mediaKey();
      }
      updateAudio();
    }, () => { if (generation === renderGeneration) advanceAudio(); }, readingSpeed.rate);
    app.eventBus.on('pagesinit', () => {
      if (generation !== renderGeneration || !current) return;
      app.pdfViewer.scrollMode = 3;
      app.pdfViewer.currentScaleValue = 'page-fit';
      app.page = page;
      ready = true;
      cancelReaderLoad(); document.title = `${current.title} · RAZ Reader`;
      updatePage(page); saveProgress();
    });
    app.eventBus.on('pagechanging', event => {
      if (generation !== renderGeneration || !ready || !current) return;
      const advancing = pendingAudioAdvance; pendingAudioAdvance = false;
      updatePage(event.pageNumber); saveProgress();
      if (advancing && audioMode === 'pages' && audioAsset()) playAudio();
    });
    app.eventBus.on('scalechanging', () => { if (generation === renderGeneration) wordLookup.close(); });
    app.eventBus.on('documenterror', () => {
      if (generation !== renderGeneration || !current) return;
      ready = false; wordLookup.close(); translation.reset(); resetNarration(); clearRecording(); updateAudio(); setReadingControls(false);
      readerFailed();
      message('这本 PDF 暂时无法打开，请返回书架尝试其他样本。');
    });
  });
});
function openBook(book) {
  center.close();
  cancelReaderLoad(); readerAttempts = 0;
  unbindWordLookup?.(); unbindWordLookup = null; wordLookup.close();
  translation.reset();
  ++coverGeneration; coverObserver?.disconnect(); ++renderGeneration; ++libraryGeneration; clearRecording();
  nativeAudio?.destroy(); nativeAudio = null;
  current = book; ready = false; viewer = null;
  pendingAudioAdvance = false; resetNarration();
  // Pick the source internally; readers only need one narration button.
  const availablePages = new Set([...Object.keys(book.audio.page_map).map(Number), ...(book.audio.embedded?.pages || [])]);
  const completePages = Array.from({ length: book.page_count }, (_, index) => index + 1).every(p => availablePages.has(p));
  audioMode = book.audio.whole_book && !completePages
    && (!availablePages.size || book.audio.whole_book.timeline?.length) ? 'whole' : 'pages';
  page = Math.max(1, Math.min(book.progress.page, book.page_count));
  $('shelf').hidden = true; $('reader').hidden = false;
  $('reading-title').textContent = book.title;
  $('word-lookup-hint').textContent = book.text_lookup_allowed ? '单击听原声 · 双击查词' : '此书暂不支持文字点读 / 查词';
  $('reading-subtitle').textContent = book.demo ? '自制测试绘本 · 系统语音，仅用于本机流程验证' : `Level ${book.level} · 书名候选 · 原 PDF 只读阅读`;
  $('page-number').textContent = '正在打开…';
  $('saved').textContent = cloudReading() ? '保存阅读进度' : '本地保存'; setReadingControls(false);
  updateAudio(); renderRecording(); loadRecordings(book);
  const images = book.reader_source.kind === 'images';
  $('pdf-frame').hidden = images; $('image-reader').hidden = !images;
  if (images) { ready = true; updatePage(page); saveProgress(); }
  else loadPDFReader();
}
function setReadingControls(enabled) {
  for (const id of ['prev', 'next', 'complete', 'translate-page']) $(id).disabled = !enabled;
  $('translate-page').disabled ||= readingOnly();
  $('record').disabled = readingOnly() || !enabled || recordPending || recorder?.state === 'recording';
}
function updatePage(value) {
  if (value !== page) {
    wordLookup.close();
    ++recordRequestId; recordPending = false; stopRecording();
  }
  const changed = page !== value;
  page = value;
  translation.refresh(changed);
  setReadingControls(ready);
  $('page-number').textContent = `第 ${page} / ${current.page_count} 页`;
  $('prev').disabled = page <= 1; $('next').disabled = page >= current.page_count;
  if (current.reader_source.kind === 'images') $('page-image').src = current.reader_source.image_pages[page - 1];
  updateAudio();
  renderRecording();
}
function synchronizePage() {
  const visiblePage = viewer?.page;
  if (ready && current && Number.isInteger(visiblePage) && visiblePage >= 1
      && visiblePage <= current.page_count && visiblePage !== page) {
    pendingAudioAdvance = false; updatePage(visiblePage); saveProgress();
  }
}
function navigate(delta) {
  if (!ready || !current) return;
  const next = Math.min(current.page_count, Math.max(1, page + delta));
  if (viewer) viewer.page = next;
  else {
    const advancing = pendingAudioAdvance; pendingAudioAdvance = false;
    updatePage(next); saveProgress();
    if (advancing && audioMode === 'pages' && audioAsset()) playAudio();
  }
}
function saveProgress(complete = false) {
  if (!current || !ready) return saveQueue;
  const book = current, snapshot = captureProgress(book, page, complete === true), body = snapshot.body;
  book.completion_days = [...new Set([...(book.completion_days || []), ...body.completion_days])];
  const sequence = ++saveSequence, generation = renderGeneration;
  book.progress = { ...body, visited: true };
  // Queue writes to prevent a slow earlier request overwriting a later page.
  $('saved').textContent = '正在保存…';
  $('retry-progress').hidden = true;
  saveQueue = saveQueue.catch(() => {}).then(async () => {
    const synced = await syncProgress(snapshot);
    if (synced && sequence === saveSequence && generation === renderGeneration) {
      $('saved').textContent = library?.mode === 'local_sync' && library.account
        ? '✓ 本机已保存 · 后台同步进度' : body.completed ? '✓ 已读完 · 已保存' : '✓ 进度已保存';
    }
  }).catch(() => {
    if (sequence === saveSequence && generation === renderGeneration && isLatestProgress(snapshot)) {
      $('saved').textContent = snapshot.backedUp ? '进度暂存在此浏览器' : '进度保存失败';
      $('retry-progress').hidden = false;
      message(cloudReading() ? '此浏览器无法保存阅读进度，请检查浏览器存储设置。'
        : snapshot.backedUp ? '服务暂未保存，已在此浏览器备份进度。可稍后重试。' : '进度未保存，请确认本地服务仍在运行。');
    }
  });
  return saveQueue;
}
function resetNarration() {
  nativeAudio?.pause(true);
  activeMediaKey = ''; narration.pause(); narration.removeAttribute('src'); narration.load();
}
function audioAsset() {
  return audioMode === 'whole' ? current?.audio.whole_book : current?.audio.page_map[String(page)]
    || (current?.audio.embedded?.pages.includes(page) ? { native: true, url: `pdf-native:${page}` } : null);
}
function audioPaused() { return audioAsset()?.native ? nativeAudio?.media()?.paused !== false : narration.paused; }
function playLabel() { return '朗读'; }
function pauseLabel() { return '暂停'; }
function mediaKey() {
  const asset = audioAsset();
  return asset && current ? `${current.edition_id}:${audioMode}:${audioMode === 'pages' ? page : ''}:${asset.url}` : '';
}
function wholeBookTimeline() { return current?.audio.whole_book?.timeline || []; }
function syncWholeBookPage() {
  if (!ready || !current || audioMode !== 'whole' || activeMediaKey !== mediaKey()
      || narration.readyState < 1 || narration.getAttribute('src') !== current.audio.whole_book?.url) return;
  const cues = wholeBookTimeline();
  // Validated boundaries, never an estimate based on total duration or page count.
  const cue = [...cues].reverse().find(cue => narration.currentTime >= cue.start);
  if (!cue || cue.page === page) return;
  pendingAudioAdvance = false;
  if (viewer) viewer.page = cue.page;
  else { updatePage(cue.page); saveProgress(); }
}
function updateAudio() {
  const a = audioAsset(), key = mediaKey();
  const hasPages = Object.keys(current?.audio.page_map || {}).length > 0 || Boolean(current?.audio.embedded?.pages.length);
  $('autoplay').disabled = !ready || audioMode !== 'pages' || !hasPages;
  $('autoplay').closest('label').hidden = audioMode === 'whole';
  $('reading-speed').hidden = !a;
  $('reading-speed').disabled = !ready || !a;
  narration.hidden = true; $('play').disabled = !a || !ready;
  $('audio-status').textContent = a?.native || a?.origin === 'pdf_embedded' ? `♫ 第 ${page} 页 PDF 内嵌音频 · 本页原声` : a ? audioMode === 'whole' ? (wholeBookTimeline().length ? '随音频翻页' : '暂无页码时间点，请手动翻页') : `♫ 第 ${page} 页音频已核验` : current?.audio.status === 'verification_failed' ? '音频核验失败 · 仍可阅读' : `第 ${page} 页无可用音频`;
  narration.dataset.page = a && audioMode === 'pages' ? String(page) : '';
  if (key !== activeMediaKey) {
    resetNarration(); activeMediaKey = key;
    if (a && !a.native) narration.src = a.url;
  }
  if (a && (a.native ? nativeAudio?.media()?.error : narration.error)) {
    $('audio-status').textContent = '音频读取失败 · 仍可阅读';
    $('play').disabled = true;
  }
  $('play').textContent = audioPaused() ? playLabel() : pauseLabel();
  renderPointReading();
}
async function playAudio() {
  wordLookup.close();
  synchronizePage();
  if (!audioAsset() || !ready) return;
  const generation = renderGeneration, key = activeMediaKey;
  try {
    if (audioAsset().native) await nativeAudio?.play();
    else await narration.play();
  } catch {
    if (generation === renderGeneration && key === activeMediaKey) message('音频未能开始播放，请再点一次朗读。');
  }
}
narration.addEventListener('play', () => { wordLookup.close(); nativeAudio?.pause(); $('recording').pause(); $('play').textContent = pauseLabel(); syncWholeBookPage(); });
narration.addEventListener('timeupdate', () => { if (!narration.paused) syncWholeBookPage(); });
narration.addEventListener('seeked', syncWholeBookPage);
narration.addEventListener('pause', () => $('play').textContent = playLabel());
narration.addEventListener('error', () => { if (narration.getAttribute('src')) { $('audio-status').textContent = '音频读取失败 · 仍可阅读'; $('play').disabled = true; } });
function advanceAudio() {
  if (!ready || audioMode !== 'pages' || activeMediaKey !== mediaKey()
      || !$('autoplay').checked || !current || page >= current.page_count) return;
  pendingAudioAdvance = true; navigate(1);
}
narration.addEventListener('ended', () => { if (narration.ended) { syncWholeBookPage(); advanceAudio(); } });
function renderPointReading() {
  $('point-reading').replaceChildren();
  if (!audioAsset()?.native) return;
  const count = nativeAudio?.records().length || 0;
  if (count <= 1) return; // The main play button already plays a single page track.
  for (let index = 0; index < count; index++) {
    const button = document.createElement('button');
    button.textContent = `🔊 点读 ${index + 1}`; button.disabled = !ready;
    button.onclick = async () => {
      const generation = renderGeneration;
      try { await nativeAudio?.play(index, true); }
      catch { if (generation === renderGeneration) message('本页内嵌音频暂时无法播放，仍可继续翻页。'); }
    };
    $('point-reading').append(button);
  }
}
$('play').onclick = () => { synchronizePage(); audioPaused() ? playAudio() : audioAsset()?.native ? nativeAudio?.pause() : narration.pause(); };
$('prev').onclick = () => { pendingAudioAdvance = false; navigate(-1); };
$('next').onclick = () => { pendingAudioAdvance = false; navigate(1); };
$('complete').onclick = () => { if (current && ready) { current.progress.completed = true; saveProgress(true); } };
$('back').onclick = async () => {
  cancelReaderLoad(); document.title = SHELF_TITLE;
  unbindWordLookup?.(); unbindWordLookup = null; wordLookup.close();
  translation.reset();
  const saved = saveProgress();
  ++renderGeneration; ready = false; current = null; viewer = null; pendingAudioAdvance = false;
  resetNarration(); clearRecording(); nativeAudio?.destroy(); nativeAudio = null;
  $('pdf-frame').src = 'about:blank'; $('reader').hidden = true; $('shelf').hidden = false;
  await saved;
  try { await loadLibrary(); } catch (e) { message(e.message); }
};
$('search').addEventListener('input', renderShelf);
$('audio-only').addEventListener('change', renderShelf);
$('retry-progress').onclick = saveProgress;
function readerShortcut(e) {
  if (!current || !ready || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey
      || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName) || e.target.isContentEditable) return;
  const dialog = document.querySelector('dialog[open]');
  if (dialog && dialog.id !== 'assessment-details') return;
  if (e.code === 'Space' || e.key === ' ') {
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.repeat) return;
    if (dialog) dialog.close();
    if (recorder?.state === 'recording') $('stop-record').click();
    else if (!recordPending && !$('record').disabled) $('record').click();
    return;
  }
  if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
    e.preventDefault(); e.stopImmediatePropagation();
    if (e.repeat) return;
    const direction = e.key === 'ArrowRight' ? 1 : -1;
    if (page + direction < 1 || page + direction > current.page_count) return;
    if (dialog) dialog.close();
    pendingAudioAdvance = false; navigate(direction);
  }
}
function preventSpaceClick(e) {
  if ((e.code === 'Space' || e.key === ' ') && current && ready && !/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)
      && !e.target.isContentEditable && !document.querySelector('dialog[open]:not(#assessment-details)')) {
    e.preventDefault(); e.stopImmediatePropagation();
  }
}
window.addEventListener('keydown', readerShortcut, true);
window.addEventListener('keyup', preventSpaceClick, true);
function flushCurrentProgress() {
  if (current && ready) flushProgress(captureProgress(current, page));
}
window.addEventListener('pagehide', () => {
  ++coverGeneration; coverObserver?.disconnect();
  flushCurrentProgress();
});
window.addEventListener('pageshow', event => {
  if (event.persisted && library && !current) { renderReadingRecords(); renderShelf(); }
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    flushCurrentProgress();
    // Background tabs can throttle timers; save now instead of recording past the cap.
    if (recordPending || recorder?.state === 'recording') {
      ++recordRequestId; recordPending = false; stopRecording(); renderRecording();
    }
  }
  else if (current && ready) saveProgress();
  else if (library) loadLibrary().catch(() => {});
});
function stopRecording() {
  recorder?.countdown?.stop();
  if (recorder && recorder.state !== 'inactive') {
    recordingFinishing.set(recorder.originKey, recorder);
    recorder.stop();
    // Keep the input alive until the final dataavailable/stop events flush it.
    // The captured onstop handler owns this stream even after a page change.
    micStream = null;
  }
  recorder = null;
  micStream?.getTracks().forEach(track => track.stop()); micStream = null;
  $('record').disabled = !ready || recordPending; $('stop-record').disabled = true;
}
function recordingKey(book, number) { return `${book.edition_id}/${number}`; }
function recordingURL(book, number, version) { return `/api/recordings/${recordingKey(book, number)}?v=${version}&scope=${encodeURIComponent(book.reader_scope || 'local')}`; }
function refreshRecording(book, assessmentFinished = false) {
  if (current?.edition_id === book.edition_id) renderRecording();
  else if (!current && assessmentFinished) loadLibrary().catch(() => {});
}
async function loadRecordings(book) {
  if (readingOnly()) return;
  recordingLoads.set(book.edition_id, 'loading'); refreshRecording(book);
  try {
    const response = await fetch(`/api/recordings/${book.edition_id}`, { headers: scopeHeaders(book.reader_scope) });
    if (!response.ok) throw new Error('Recording list unavailable');
    const { pages } = await response.json();
    for (const [number, metadata] of Object.entries(pages)) {
      const key = recordingKey(book, number), local = pageRecordings.get(key);
      if (!local || (local.status === 'saved' && metadata.version > local.version)) {
        pageRecordings.set(key, { version: metadata.version, status: 'saved',
          assessment: metadata.assessment,
          url: recordingURL(book, number, metadata.version) });
      }
    }
    recordingLoads.set(book.edition_id, 'loaded');
  } catch {
    recordingLoads.set(book.edition_id, 'failed');
  }
  refreshRecording(book);
}
async function persistRecording(book, number, clip) {
  const key = recordingKey(book, number);
  clip.status = 'saving'; refreshRecording(book);
  try {
    const reference = await clip.reference;
    const headers = { 'Content-Type': clip.blob.type, 'X-Recording-Version': String(clip.version), ...scopeHeaders(book.reader_scope) };
    const encodedReference = reference?.text && encodeURIComponent(reference.text);
    if (encodedReference && encodedReference.length <= 8192) headers['X-Assessment-Text'] = encodedReference;
    const response = await fetch(`/api/recordings/${key}`, { method: 'POST',
      headers, body: clip.blob });
    if (!response.ok) throw new Error('Local recording save failed');
    const combined = response.headers.get('X-Recording-Saved') === String(clip.version);
    // The server flushes this acknowledgement as soon as the original audio is safe.
    // Its same response then carries the score, avoiding a second network request.
    const result = combined ? { saved: true } : await response.json();
    if (!result.saved && !result.stale) throw new Error('Recording save not acknowledged');
    // A slow completed take must not replace a newer take in either UI or SQLite.
    if (pageRecordings.get(key) !== clip) {
      if (combined) response.body?.cancel().catch(() => {});
      URL.revokeObjectURL(clip.url); return;
    }
    URL.revokeObjectURL(clip.url);
    if (result.stale) {
      pageRecordings.delete(key); await loadRecordings(book);
    } else {
      clip.url = recordingURL(book, number, clip.version); clip.status = 'saved'; delete clip.blob;
      if (clip.reference) assessment.evaluate(book, number, clip, clip.reference, combined ? response : null);
    }
  } catch {
    clip.status = 'failed';
  }
  refreshRecording(book);
}
function renderRecording() {
  const storage = cloudReading() ? '云端' : '本机';
  const clip = current && pageRecordings.get(recordingKey(current, page));
  const loadState = current && recordingLoads.get(current.edition_id);
  const finishing = current && recordingFinishing.has(recordingKey(current, page));
  const playable = clip && clip.status !== 'saving' && !finishing && !recordPending && recorder?.state !== 'recording';
  const source = playable ? clip.url : null;
  $('record').hidden = recorder?.state === 'recording';
  $('stop-record').hidden = recorder?.state !== 'recording';
  $('recording-countdown').hidden = recorder?.state !== 'recording';
  $('record').textContent = clip ? '重读' : recordPending ? '准备' : '跟读';
  $('recording').setAttribute('aria-label', `我的第 ${page} 页录音`);
  recordingTransport.setSource(source);
  assessment.render(current, page, clip, playable);
  $('recording').dataset.page = playable ? String(page) : '';
  $('retry-recording').hidden = recorder?.state === 'recording' || recordPending
    || !(clip?.status === 'failed' || loadState === 'failed');
  $('record-status').textContent = recorder?.state === 'recording' ? `正在录第 ${page} 页…`
    : recordPending ? `正在准备第 ${page} 页录音…`
    : finishing ? `第 ${page} 页录音 · 正在结束并保存…`
    : clip?.status === 'saving' ? `第 ${page} 页录音 · 正在保存到${storage}…`
    : clip?.status === 'failed' ? `第 ${page} 页录音保存失败 · 请重试；关闭页面将丢失这次新录音`
    : clip ? `第 ${page} 页录音 · 已保存到${storage} · 仅保留最新一条`
    : loadState === 'failed' ? '录音读取失败 · 请重试'
    : loadState === 'loading' ? '正在读取录音…' : `第 ${page} 页还没有录音`;
}
function clearRecording() {
  ++recordRequestId; recordPending = false; stopRecording();
  recordingTransport.setSource(null);
  assessment.render(null, 0, null, false);
  // Completed clips belong to their captured edition/page, even after leaving the reader.
  $('retry-recording').hidden = true;
  $('record-status').textContent = cloudReading() ? (library.account ? '录音随账号保存，每页保留最新一条' : '游客录音保存到云端，可在此浏览器回听') : '每页录音保存在本机，仅保留最新一条';
}
$('retry-recording').onclick = () => {
  if (!current) return;
  const clip = pageRecordings.get(recordingKey(current, page));
  if (clip?.status === 'failed') persistRecording(current, page, clip);
  else loadRecordings(current);
};
$('record').onclick = async () => {
  synchronizePage();
  if (!current || !ready || recordPending || recorder?.state === 'recording') return;
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return message('当前浏览器不支持录音，请使用新版 Chrome 或 Safari。');
  const generation = renderGeneration, originBook = current, originPage = page, requestId = ++recordRequestId;
  const key = recordingKey(originBook, originPage);
  const reference = assessmentReference(originBook, viewer?.pdfDocument, originPage);
  const audioSeconds = pageAudioSeconds(originBook, originPage);
  recordPending = true; $('record').disabled = true;
  wordLookup.close(); narration.pause(); nativeAudio?.pause(); $('recording').pause(); renderRecording();
  let stream;
  const obsolete = () => !current || generation !== renderGeneration || requestId !== recordRequestId || page !== originPage;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    if (obsolete()) { stream.getTracks().forEach(t => t.stop()); return; }
    micStream = stream;
    const response = await fetch(`/api/recordings/${key}/take`, { method: 'POST', headers: scopeHeaders(originBook.reader_scope) });
    if (!response.ok) throw new Error('Recording storage unavailable');
    const { version } = await response.json();
    const pageReference = await reference;
    if (obsolete()) { stream.getTracks().forEach(t => t.stop()); return; }
    const seconds = recordingLimit(pageReference.text, audioSeconds);
    recordPending = false;
    micStream = stream; const sessionRecorder = new MediaRecorder(stream); recorder = sessionRecorder;
    sessionRecorder.originKey = key;
    const chunks = [];
    sessionRecorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    sessionRecorder.onstop = () => {
      if (recordingFinishing.get(key) === sessionRecorder) recordingFinishing.delete(key);
      stream.getTracks().forEach(t => t.stop()); sessionRecorder.countdown?.stop();
      if (recorder === sessionRecorder) stopRecording();
      if (!chunks.length) { refreshRecording(originBook); return; }
      const previous = pageRecordings.get(key);
      const blob = new Blob(chunks, { type: sessionRecorder.mimeType });
      const clip = { blob, version, reference, status: 'saving', url: URL.createObjectURL(blob) };
      if (!previous || previous.version < version) {
        pageRecordings.set(key, clip);
        if (previous?.url.startsWith('blob:')) URL.revokeObjectURL(previous.url);
      }
      persistRecording(originBook, originPage, clip);
    };
    sessionRecorder.start(); $('stop-record').disabled = false; renderRecording();
    sessionRecorder.countdown = recordingCountdown(seconds, {
      tick: (left, total) => {
        if (recorder !== sessionRecorder) return;
        const clock = $('recording-countdown');
        clock.textContent = `${left}s`; clock.dataset.limit = String(total);
        clock.classList.toggle('ending', left <= 5);
        clock.title = `本页录音上限 ${total} 秒，到时自动保存并评测`;
      },
      finish: () => { if (recorder === sessionRecorder) { stopRecording(); renderRecording(); } },
    });
  } catch {
    stream?.getTracks().forEach(t => t.stop());
    if (generation === renderGeneration && current && requestId === recordRequestId) {
      recordPending = false; stopRecording(); renderRecording(); message('无法开始录音，请检查麦克风权限及网络连接。');
    }
  }
};
$('stop-record').onclick = () => { stopRecording(); renderRecording(); };
loadLibrary().catch(e => message(e.message));

// One shared transport for verified page/whole-book audio and native PDF media.
function updateTimeline() {
  const media = audioAsset()?.native ? nativeAudio?.media() : narration;
  const duration = Number.isFinite(media?.duration) ? media.duration : 0;
  const elapsed = Number.isFinite(media?.currentTime) ? media.currentTime : 0;
  const format = value => `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
  $('audio-seek').disabled = !duration || !ready;
  $('audio-seek').max = duration || 1; $('audio-seek').value = elapsed;
  $('audio-time').textContent = `${format(elapsed)} / ${format(duration)}`;
  $('audio-seek').hidden = $('audio-time').hidden = !audioAsset();
}
$('audio-seek').oninput = () => {
  const media = audioAsset()?.native ? nativeAudio?.media() : narration;
  if (Number.isFinite(media?.duration)) media.currentTime = Number($('audio-seek').value);
};
setInterval(() => { if (current && ready) updateTimeline(); }, 250);
// Refresh the day even if the shelf stays open overnight. Visibility changes
// already reload records after sleep/background timer throttling.
setInterval(() => {
  if (library && localDay() !== renderedReadingDay) renderReadingRecords();
}, 1000);
