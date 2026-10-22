import { readingState, mostRecentFirst } from '/reading.js';
import { scopeHeaders } from '/identity.js';
const $ = id => document.getElementById(id);
const displayName = account => account.display_name || account.username;

export function personalCenter({ openBook, getLevel, beforeAuth }) {
  let library, mode = 'login', busy = false, adding = false, editing = false;
  // Capture only at page creation. A late library refresh in the old page must
  // not consume the flag written just before an account-switch navigation.
  let reopenAfterReload = false;
  try {
    reopenAfterReload = Boolean(sessionStorage.getItem('little-reader:show-personal'));
    sessionStorage.removeItem('little-reader:show-personal');
  } catch { /* Optional convenience. */ }
  const dialog = $('personal-center');
  $('open-personal-center').onclick = () => { if (library) dialog.showModal(); };
  $('close-personal-center').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { $('account-password').value = ''; adding = false; editing = false; if (library) renderAccounts(); });
  function setMode(value) {
    mode = value;
    $('account-login-tab').setAttribute('aria-pressed', String(mode === 'login'));
    $('account-register-tab').setAttribute('aria-pressed', String(mode === 'register'));
    $('account-import-label').hidden = mode !== 'register' || Boolean(library?.account);
    $('account-display-name-label').hidden = mode !== 'register';
    $('account-display-name').disabled = mode !== 'register';
    $('account-password').autocomplete = mode === 'register' ? 'new-password' : 'current-password';
    $('account-submit').textContent = mode === 'register' ? '创建我的账号' : '登录并同步';
    $('account-error').hidden = true;
  }
  $('account-login-tab').onclick = () => { if (!busy) setMode('login'); };
  $('account-register-tab').onclick = () => { if (!busy) setMode('register'); };
  $('account-add').onclick = () => {
    if (busy) return;
    adding = true; editing = false; setMode('login'); renderAccounts();
    $('account-username').value = ''; $('account-password').value = ''; $('account-display-name').value = ''; $('account-import').checked = false;
    $('account-username').focus();
  };
  $('account-cancel-add').onclick = () => { adding = false; $('account-password').value = ''; renderAccounts(); };
  $('account-edit-name').onclick = () => {
    if (busy || !library.account) return;
    editing = true; adding = false; renderAccounts();
    $('account-profile-name').value = library.account.display_name || '';
    $('account-error').hidden = true; $('account-profile-name').focus();
  };
  $('account-profile-cancel').onclick = () => { editing = false; renderAccounts(); };
  $('account-profile-form').onsubmit = event => {
    event.preventDefault(); authenticate('profile', { display_name: $('account-profile-name').value.trim() });
  };
  function renderAccounts() {
    const saved = library.saved_accounts || [];
    $('account-guest').hidden = !library.accounts_enabled || Boolean((library.account || saved.length) && !adding);
    $('account-cancel-add').hidden = !adding;
    $('account-import-label').hidden = mode !== 'register' || Boolean(library.account);
    $('account-switcher').hidden = !library.accounts_enabled || (!library.account && !saved.length);
    $('account-add').hidden = adding;
    $('account-profile-form').hidden = !library.account || !editing;
    $('saved-accounts').replaceChildren();
    for (const account of saved) {
      const label = displayName(account);
      const description = label === account.username ? label : `${label}（${account.username}）`;
      const row = document.createElement('div'); row.className = 'saved-account' + (account.active ? ' active' : '');
      const button = document.createElement('button'); button.className = 'saved-account-main'; button.dataset.accountUsername = account.username;
      button.setAttribute('aria-label', account.active ? `${description}，当前账号` : `切换到 ${description}`);
      button.setAttribute('aria-pressed', String(account.active)); button.dataset.accountAction = 'switch';
      const avatar = document.createElement('span'); avatar.className = 'account-avatar'; avatar.textContent = Array.from(label)[0].toUpperCase(); avatar.setAttribute('aria-hidden', 'true');
      const name = document.createElement('span'); name.className = 'saved-account-name'; name.textContent = label; name.title = `${label}（${account.username}）`;
      if (account.display_name && account.display_name !== account.username) {
        const username = document.createElement('small'); username.textContent = account.username; name.append(username);
      }
      button.append(avatar, name);
      if (account.active) { const current = document.createElement('span'); current.className = 'saved-account-current'; current.textContent = '当前'; button.append(current); }
      button.onclick = () => { if (!account.active) authenticate('switch', { username: account.username }); };
      row.append(button);
      if (!account.active) {
        const remove = document.createElement('button'); remove.className = 'saved-account-remove'; remove.textContent = '移除';
        remove.setAttribute('aria-label', `从此设备移除 ${description}`); remove.dataset.accountAction = 'forget';
        remove.onclick = () => authenticate('forget', { username: account.username }); row.append(remove);
      }
      $('saved-accounts').append(row);
    }
  }
  async function authenticate(action, body) {
    if (busy) return;
    busy = true;
    const controls = [...dialog.querySelectorAll('[data-account-action]'), ...['account-submit', 'account-logout', 'account-login-tab', 'account-register-tab', 'account-add', 'account-cancel-add', 'account-edit-name', 'account-profile-save', 'account-profile-cancel'].map($)];
    for (const button of controls) button.disabled = true;
    dialog.setAttribute('aria-busy', 'true');
    $('account-error').hidden = true;
    try {
      await beforeAuth(action, body);
      const response = await fetch('/api/account/' + action, { method: 'POST',
        headers: { 'Content-Type': 'application/json', ...scopeHeaders(library.reader_scope) }, body: JSON.stringify(body) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '操作未完成，请稍后再试。');
      // Reload cancels the old identity's media and in-memory state. Other tabs
      // also reload; pending writes carry their original scope and cannot mix.
      try { localStorage.setItem('little-reader:account-change', crypto.randomUUID()); sessionStorage.setItem('little-reader:show-personal', '1'); } catch { /* Storage can be unavailable. */ }
      location.reload();
    } catch (error) {
      $('account-error').textContent = error.message || '网络连接失败，请稍后再试。';
      $('account-error').hidden = false;
    } finally {
      busy = false;
      for (const button of controls) button.disabled = false;
      dialog.removeAttribute('aria-busy');
    }
  }
  $('account-form').onsubmit = event => {
    event.preventDefault();
    authenticate(mode, { username: $('account-username').value.trim(), password: $('account-password').value,
      ...(mode === 'register' ? { display_name: $('account-display-name').value.trim() } : {}),
      import_progress: !library.account && $('account-import').checked, level: library.account ? 'all' : getLevel() });
  };
  $('account-logout').onclick = () => authenticate('logout', {});
  window.addEventListener('storage', event => { if (event.key === 'little-reader:account-change') location.reload(); });
  return {
    close: () => dialog.close(),
    update(value) {
      library = value;
      renderAccounts();
      $('account-member').hidden = !value.account;
      $('account-name').textContent = value.account ? displayName(value.account) : '';
      $('account-username-caption').textContent = value.account ? `账号：${value.account.username}` : '';
      $('account-edit-name').textContent = value.account?.display_name ? '修改昵称' : '设置昵称';
      const entryLabel = value.account ? displayName(value.account) : (value.accounts_enabled ? '登陆' : '我的');
      $('personal-entry-label').textContent = entryLabel;
      $('open-personal-center').title = entryLabel;
      $('open-personal-center').setAttribute('aria-label', `${entryLabel}，进入个人中心`);
      $('account-caption').textContent = value.account ? '每次打开，都从上次继续' : value.accounts_enabled ? '游客阅读 · 登录后可跨设备同步' : '本机阅读进度';
      document.querySelector('.reading-stats small').textContent = value.account ? '此账号的阅读记录' : value.accounts_enabled ? '游客记录保存在此浏览器对应的空间' : '本机阅读记录';
      if (value.mode === 'local_sync') document.querySelector('.reading-stats small').textContent =
        !value.account ? '本机评分 · 登陆后同步阅读记录' : value.sync_pending || !value.sync_online
          ? '本机评分 · 有记录待同步，联网后自动重试' : '本机评分 · 进度、录音和成绩已同步';
      $('personal-levels').replaceChildren();
      for (const level of value.levels) {
        const books = value.books.filter(b => !b.demo && b.level === level.level);
        if (!books.length) continue;
        const complete = books.filter(b => b.progress.completed).length;
        const row = document.createElement('div'), label = document.createElement('div'), name = document.createElement('span'), count = document.createElement('small');
        label.className = 'personal-level-label'; name.textContent = `Level ${level.level}`; count.textContent = `${complete} / ${books.length} 本读完`;
        label.append(name, count);
        const bar = document.createElement('div'), fill = document.createElement('span'); bar.className = 'personal-progress';
        bar.setAttribute('role', 'progressbar'); bar.setAttribute('aria-label', `Level ${level.level} 已读完`);
        bar.setAttribute('aria-valuemin', '0'); bar.setAttribute('aria-valuemax', String(books.length)); bar.setAttribute('aria-valuenow', String(complete));
        fill.style.width = `${complete / books.length * 100}%`; bar.append(fill); row.append(label, bar); $('personal-levels').append(row);
      }
      const visited = value.books.filter(b => readingState(b) !== 'new').sort(mostRecentFirst);
      $('personal-empty').hidden = visited.length > 0;
      $('personal-books').replaceChildren();
      for (const book of visited) {
        const button = document.createElement('button'), text = document.createElement('div'), title = document.createElement('strong'), detail = document.createElement('small'), arrow = document.createElement('span');
        button.className = 'personal-book'; title.textContent = book.title; arrow.textContent = '›'; arrow.setAttribute('aria-hidden', 'true');
        detail.textContent = `Level ${book.level} · ${book.progress.completed ? '已读完' : `第 ${book.progress.page} / ${book.page_count} 页`}${book.assessment_summary?.scored_pages ? ` · 平均 ${book.assessment_summary.average} 分` : ''}`;
        text.append(title, detail); button.append(text, arrow); button.disabled = book.validation === 'failed';
        button.onclick = () => { dialog.close(); openBook(book); }; $('personal-books').append(button);
      }
      if (reopenAfterReload) { reopenAfterReload = false; dialog.showModal(); }
    },
  };
}
