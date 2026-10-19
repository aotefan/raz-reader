// One small native dialog: keyboard focus stays trapped, Escape returns focus,
// and touch dismissal is restricted to the handle so list scrolling stays natural.
export function selectionSheet() {
  const dialog = document.getElementById('selection-sheet');
  const content = document.getElementById('selection-options');
  let active, dragStart = null;
  const close = () => dialog.close();
  document.getElementById('close-selection-sheet').onclick = close;
  dialog.addEventListener('click', event => {
    const box = dialog.getBoundingClientRect();
    if (event.target === dialog && (event.clientX < box.left || event.clientX > box.right
      || event.clientY < box.top || event.clientY > box.bottom)) close();
  });
  dialog.addEventListener('close', () => {
    dialog.style.transform = ''; dragStart = null;
    active?.trigger.setAttribute('aria-expanded', 'false');
  });
  const handle = document.getElementById('selection-handle');
  handle.onpointerdown = event => {
    if (!matchMedia('(max-width:760px)').matches) return;
    dragStart = event.clientY; handle.setPointerCapture(event.pointerId);
  };
  handle.onpointermove = event => {
    if (dragStart === null) return;
    dialog.style.transform = `translateY(${Math.max(0, event.clientY - dragStart)}px)`;
  };
  handle.onpointerup = event => {
    const distance = dragStart === null ? 0 : event.clientY - dragStart;
    dragStart = null; dialog.style.transform = '';
    if (distance > 64) close();
  };
  handle.onpointercancel = () => { dragStart = null; dialog.style.transform = ''; };
  return {
    configure(trigger, config) {
      trigger.dataset.value = config.value;
      const choice = config.options.find(option => option.value === config.value);
      trigger.querySelector('.picker-label').textContent = choice?.label || config.title;
      trigger.setAttribute('aria-label', `${config.title}：${choice?.label || ''}`);
      trigger.onclick = () => {
        active = { trigger, config };
        document.getElementById('selection-title').textContent = config.title;
        content.replaceChildren();
        content.className = config.layout === 'list' ? 'sheet-list' : 'sheet-levels';
        let group;
        for (const option of config.options) {
          if (option.group && option.group !== group) {
            const heading = document.createElement('h3'); heading.textContent = option.group;
            content.append(heading); group = option.group;
          }
          const button = document.createElement('button');
          button.className = 'sheet-choice'; button.dataset.choiceValue = option.value;
          button.disabled = Boolean(option.disabled);
          button.setAttribute('aria-pressed', String(option.value === config.value));
          if (option.value === 'all' && config.layout !== 'list') button.classList.add('sheet-all');
          const label = document.createElement('span'); label.className = 'choice-label'; label.textContent = option.label;
          const check = document.createElement('span'); check.className = 'choice-check'; check.textContent = '✓'; check.setAttribute('aria-hidden', 'true');
          button.append(label);
          if (option.detail) { const detail = document.createElement('small'); detail.textContent = option.detail; button.append(detail); }
          button.append(check);
          button.onclick = () => { close(); config.choose(option.value); };
          content.append(button);
        }
        trigger.setAttribute('aria-expanded', 'true');
        dialog.showModal(); content.scrollTop = 0;
        if (config.options.find(option => option.value === config.value)?.group === '待上传'
          || config.options.find(option => option.value === config.value)?.group === '待整理') {
          content.querySelector('[aria-pressed="true"]')?.scrollIntoView({ block: 'nearest' });
        }
      };
    },
  };
}
