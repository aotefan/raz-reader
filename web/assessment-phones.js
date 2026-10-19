// OpenPronounce confidence is a weighted error signal, NOT pronunciation accuracy.
// 0.4 is a display emphasis threshold; it does not change the score or word flags.
export function phoneFeedback(phone) {
  const expected = typeof phone.expected === 'string' ? phone.expected : '';
  const heard = typeof phone.heard === 'string' ? phone.heard : '';
  const same = expected && expected === heard;
  const practice = !same && Number.isFinite(phone.confidence) && phone.confidence >= .4;
  return { expected, heard, state: same ? 'matched' : practice ? 'practice' : 'uncertain',
    label: same ? '匹配' : !practice ? '待确认' : heard ? '有差异' : '未听清',
    note: same ? `/${expected}/：本次识别与目标音一致。`
      : !practice ? `/${expected}/：这处识别不确定，暂不判为读错。`
      : heard ? `目标 /${expected}/，本次识别为 /${heard}/。可以再听一遍单词。`
      : `目标 /${expected}/，本次没有识别到这个音。可能漏读或没有听清。` };
}

export function wordNeedsPractice(word) {
  return word.status === 'practice' || (Array.isArray(word.phones)
    && word.phones.some(phone => phoneFeedback(phone).state === 'practice'));
}

export function showPhones(word) {
  const section = document.getElementById('assessment-phones');
  const list = document.getElementById('assessment-phone-list');
  const note = document.getElementById('assessment-phone-note');
  section.hidden = false;
  const phones = Array.isArray(word.phones) ? word.phones : [];
  list.replaceChildren(...phones.map(phone => {
    const feedback = phoneFeedback(phone), button = document.createElement('button');
    button.className = `phone-detail ${feedback.state}`;
    button.setAttribute('aria-label', feedback.note);
    button.setAttribute('aria-pressed', 'false');
    for (const [role, text] of [['target', feedback.expected || '—'], ['heard', feedback.heard || '—'], ['label', feedback.label]]) {
      const item = document.createElement('span'); item.className = `phone-${role}`;
      item.textContent = text; button.append(item);
    }
    button.onclick = () => {
      list.querySelectorAll('button').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      note.textContent = feedback.note;
    };
    return button;
  }));
  section.querySelector('.phone-caption').hidden = !phones.length;
  note.textContent = phones.length ? '点音标查看差异；识别结果仅供练习参考。'
    : word.status === 'matched' ? '本次没有标出需要练习的音。'
    : '这条评分没有逐音明细，重新跟读后可以查看。';
}
