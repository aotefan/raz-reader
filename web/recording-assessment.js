import { scopeHeaders } from '/identity.js';
import { readingText } from '/ocr-text.js';
import { showPhones, wordNeedsPractice } from '/assessment-phones.js';
import { assessmentReward } from '/assessment-reward.js';
import { assessmentPassScore } from '/reward-model.js';

// Capture the document/page at recording start, before navigation can change them.
export async function assessmentReference(book, document, number) {
  try {
    const recognized = await readingText(book, number);
    return { text: recognized.reading.text };
  } catch (error) {
    const message = error.message || '本页文字读取失败，请重试。';
    return { error: message.includes('评分') ? message : message + '录音会保留，暂不评分。' };
  }
}

export function recordingAssessment(context, refresh) {
  const $ = id => document.getElementById(id), button = $('assess-recording');
  const panel = $('assessment-details'), status = $('assessment-status');
  const audio = $('assessment-word-audio');
  const reward = assessmentReward(button);
  let visibleKey = '', speechId = 0, speechURL, selectedWord, selectedItem;
  const recording = $('recording');
  function replayLabel() { $('assessment-replay').textContent = recording.paused ? '回放我的录音' : '暂停回放'; }
  recording.addEventListener('play', replayLabel);
  recording.addEventListener('pause', replayLabel);
  recording.addEventListener('ended', replayLabel);
  $('assessment-replay').onclick = () => { stopWord(); $('recording-play').click(); };
  // Reuse the page's original-audio player, including its pause/rate handling.
  const narrate = $('assessment-narrate'), pagePlay = $('play');
  const narrationLabel = () => { narrate.textContent = pagePlay.textContent; narrate.disabled = pagePlay.disabled; };
  new MutationObserver(narrationLabel).observe(pagePlay, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
  narrate.onclick = () => { stopWord(); recording.pause(); pagePlay.click(); narrationLabel(); };
  narrationLabel();
  $('assessment-demo').onclick = () => { if (selectedWord) hearWord(selectedWord, selectedItem); };
  function stopWord() {
    ++speechId; audio.pause(); audio.removeAttribute('src'); audio.load();
    if (speechURL) URL.revokeObjectURL(speechURL);
    speechURL = null;
  }
  function close(restoreFocus = false) {
    if (panel.open) panel.close();
    stopWord(); button.setAttribute('aria-expanded', 'false');
    if (restoreFocus && !button.hidden && !button.disabled) button.focus({ preventScroll: true });
  }
  panel.addEventListener('close', () => { stopWord(); recording.pause(); button.setAttribute('aria-expanded', 'false'); });
  panel.addEventListener('cancel', event => { event.preventDefault(); close(true); });
  panel.addEventListener('click', event => {
    const rect = panel.getBoundingClientRect();
    if (event.target === panel && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close(true);
  });
  $('close-assessment').onclick = () => close(true);
  $('assessment-practice').onclick = () => { close(); $('record').click(); };
  $('assessment-next').onclick = () => {
    if ($('next').disabled) return;
    close(); $('next').click();
  };
  function selectWord(word, item) {
    stopWord();
    selectedWord = word; selectedItem = item;
    $('assessment-words').querySelectorAll('[aria-pressed]').forEach(node => node.setAttribute('aria-pressed', String(node === item)));
    $('assessment-word-help').hidden = false;
    $('assessment-word').textContent = word.word;
    $('assessment-phonetic').textContent = word.expected ? `/${word.expected}/` : '';
    $('assessment-word-status').textContent = '';
    showPhones(word);
  }
  async function hearWord(word, item) {
    recording.pause();
    selectWord(word, item); const id = speechId;
    $('assessment-word-status').textContent = '正在准备发音…';
    try {
      const response = await fetch('/api/pronounce', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ word: word.word }) });
      if (!response.ok) throw new Error('单词发音暂不可用，请再点一次。');
      const blob = await response.blob();
      if (id !== speechId || !panel.open) return;
      speechURL = URL.createObjectURL(blob); audio.src = speechURL;
      await audio.play();
      if (id === speechId) $('assessment-word-status').textContent = '单词发音';
    } catch (error) {
      if (id === speechId && panel.open) $('assessment-word-status').textContent = error.message;
    }
  }
  function details(result) {
    $('assessment-title').textContent = '跟读反馈';
    const practice = result.words.filter(wordNeedsPractice).length;
    $('assessment-score').textContent = result.score;
    $('assessment-score-ring').style.setProperty('--score', `${Math.max(0, Math.min(100, result.score))}%`);
    const belowPass = result.score < assessmentPassScore;
    $('assessment-score-ring').classList.toggle('below-pass', belowPass);
    $('assessment-score-ring').setAttribute('aria-label', `${result.score} 分，${belowPass ? '未达标' : '已达标'}，满分 100`);
    $('assessment-score-ring').querySelector('small').textContent = belowPass ? '未达标 · 80 分达标' : '满分 100';
    $('assessment-summary').textContent = practice ? `${result.words.length} 个词中，${practice} 个再练练` : belowPass ? '再练一次，会更熟练' : '这次读得很棒！';
    selectedWord = null; selectedItem = null;
    $('assessment-demo').disabled = !result.words.length;
    replayLabel();
    $('assessment-word-help').hidden = true;
    $('assessment-phones').hidden = true;
    $('assessment-words').replaceChildren(...result.words.map(word => {
      const item = document.createElement('button');
      item.textContent = word.word; item.className = wordNeedsPractice(word) ? 'practice' : word.status;
      item.setAttribute('aria-pressed', 'false');
      item.setAttribute('aria-label', `${word.word}${wordNeedsPractice(word) ? '，建议练习' : ''}，听单词发音`);
      item.onclick = () => hearWord(word, item);
      return item;
    }));
    $('assessment-tip').textContent = practice
      ? '点红色单词，听示范、看发音差异。' : '点单词，再听一遍标准发音。';
    const index = Math.max(0, result.words.findIndex(wordNeedsPractice));
    if (result.words[index]) selectWord(result.words[index], $('assessment-words').children[index]);
  }
  function openDetails(result) {
    details(result);
    $('assessment-next').disabled = $('next').disabled;
    if (!panel.open) panel.showModal();
    $('assessment-body').scrollTop = 0;
    button.setAttribute('aria-expanded', 'true');
  }
  function render(book, page, clip, enabled) {
    const key = `${book?.edition_id}/${page}/${clip?.version}`;
    if (key !== visibleKey || !enabled) { close(); reward.stop(); visibleKey = key; }
    if (enabled && clip?.assessment && !clip.referenceChecked) verifySavedReference(book, page, clip);
    button.hidden = !enabled || clip?.status !== 'saved';
    status.hidden = button.hidden || !clip?.assessmentError;
    status.textContent = clip?.assessmentError || '';
    button.disabled = clip?.assessing || clip?.checkingReference || false;
    button.removeAttribute('aria-label');
    button.textContent = clip?.assessing || clip?.checkingReference ? '评测中…' : clip?.assessment
      ? `${clip.assessment.score} 分` : clip?.assessmentError ? '重试评测' : '评测';
    if (!button.hidden && !button.disabled && clip?.assessment) reward.decorate(clip.assessment.score);
    button.setAttribute('aria-expanded', String(panel.open));
  }
  async function verifySavedReference(book, number, clip) {
    const origin = context();
    if (!origin.document || origin.book !== book || origin.number !== number || origin.clip !== clip) return;
    clip.referenceChecked = true; clip.checkingReference = true;
    try {
      const reference = await assessmentReference(book, origin.document, number);
      const current = context();
      if (current.book !== book || current.number !== number || current.clip !== clip) {
        clip.referenceChecked = false; return;
      }
      if (reference.error) {
        // A historic low score is not valid evidence when its answer key cannot
        // be confirmed. Keep the take playable and permit a later retry.
        clip.assessment = null;
        clip.assessmentError = reference.error;
      } else if (reference.text !== clip.assessment?.reference) {
        // Reuse the saved take/version. Never upload again or adjust the old score.
        clip.assessment = null;
        await evaluate(book, number, clip, Promise.resolve(reference), null, false);
      }
    } finally {
      clip.checkingReference = false; refresh(book);
    }
  }
  async function evaluate(book, number, clip, reference, uploadResponse = null, celebrate = true) {
    if (clip.assessing) return;
    clip.assessing = true; clip.assessmentError = ''; refresh(book);
    const rewardToken = reward.token();
    try {
      const result = await reference;
      if (result.error) throw new Error(result.error);
      const response = uploadResponse || await fetch('/api/assessment', { method: 'POST',
        headers: { 'Content-Type': 'application/json', ...scopeHeaders(book.reader_scope) },
        body: JSON.stringify({ edition_id: book.edition_id, page: number, version: clip.version, text: result.text }) });
      const payload = await response.json().catch(error => {
        if (uploadResponse) throw new Error('评测结果接收中断，录音已保存，请重试评测。');
        throw error;
      });
      if (!response.ok || payload.assessment_error) throw new Error(payload.assessment_error || payload.error || '评测暂不可用，请重试。');
      const assessment = uploadResponse ? payload.assessment : payload;
      if (!assessment) throw new Error('评测结果未接收完整，请重试评测。');
      if (assessment.version !== clip.version) throw new Error('录音版本已变化，请重试。');
      clip.assessment = assessment;
      clip.referenceChecked = true;
    } catch (error) { clip.assessmentError = error.message || '评测暂不可用，请重试。'; }
    finally {
      clip.assessing = false; refresh(book, true);
      const current = context();
      if (celebrate && !clip.assessmentError && current.book?.edition_id === book.edition_id
        && current.number === number && current.clip === clip && !button.hidden
        && reward.token() === rewardToken && !document.hidden) {
        reward.celebrate(clip.assessment?.score);
        if (clip.assessment?.words.some(wordNeedsPractice)) openDetails(clip.assessment);
      }
    }
  }
  button.onclick = () => {
    const { book, number, clip, document } = context();
    if (!book || !clip || clip.status !== 'saved') return;
    if (clip.assessment) { reward.stop(); openDetails(clip.assessment); }
    else evaluate(book, number, clip, assessmentReference(book, document, number));
  };
  return { render, evaluate };
}
