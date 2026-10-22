const { test, expect } = require('@playwright/test');

// Browser plugin not available: Playwright uses only the generated original demo.
// Controlled microphone/assessment fixtures test interactions, not model accuracy.
test('feedback narration, compact actions and recording/page keyboard shortcuts', async ({ page, request }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  const library = await (await request.get('/api/library')).json();
  const book = library.books.find(b => b.title === 'A Little Garden');
  expect(book.demo).toBe(true);
  await request.post('/api/progress', { data: { edition_id: book.edition_id, page: 2 } });
  const wav = Buffer.alloc(44 + 32000);
  wav.write('RIFF'); wav.writeUInt32LE(wav.length - 8, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(16000, 24); wav.writeUInt32LE(32000, 28);
  wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write('data', 36); wav.writeUInt32LE(32000, 40);
  await page.addInitScript(base64 => {
    const blob = new Blob([Uint8Array.from(atob(base64), c => c.charCodeAt(0))], { type: 'audio/wav' });
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
    } });
    window.MediaRecorder = class {
      constructor() { this.state = 'inactive'; this.mimeType = 'audio/wav'; }
      start() { this.state = 'recording'; }
      stop() { this.state = 'inactive'; queueMicrotask(() => { this.ondataavailable({ data: blob }); this.onstop(); }); }
    };
  }, wav.toString('base64'));
  let version = 0;
  const reference = 'I plant a little seed.';
  await page.route('**/api/recordings/**', async route => {
    const req = route.request(), pathname = new URL(req.url()).pathname;
    if (pathname.endsWith('/take')) return route.fulfill({ json: { version: ++version } });
    if (req.method() === 'POST') return route.fulfill({ json: { saved: true, version } });
    if (pathname.endsWith('/' + book.edition_id)) return route.fulfill({ json: { pages: {} } });
    return route.fulfill({ contentType: 'audio/wav', body: wav });
  });
  await page.route('**/api/assessment', async route => {
    expect(route.request().postDataJSON().text).toBe(reference);
    return route.fulfill({ json: { version, reference, score: 85,
      words: reference.replace('.', '').split(' ').map(word => ({ word, status: 'good', phones: [] })) } });
  });
  await page.goto('/');
  await page.locator('#search').fill('A Little');
  await page.keyboard.press('Space');
  await expect(page.locator('#search')).toHaveValue('A Little ');
  await page.getByRole('button', { name: '打开 A Little Garden' }).click();
  await expect(page.locator('#record')).toBeEnabled();
  await page.frameLocator('#pdf-frame').locator('#viewerContainer').click({ position: { x: 20, y: 20 } });
  await page.keyboard.press('Space');
  await expect(page.locator('#stop-record')).toBeEnabled();
  await page.keyboard.press('Space');
  await expect(page.locator('#assess-recording')).toHaveText('85 分');
  await expect(page.locator('#record')).toHaveText('重读');
  await page.locator('#assess-recording').click();
  await page.locator('#assessment-narrate').click();
  await expect.poll(() => page.locator('#narration').evaluate(a => !a.paused && a.currentTime > 0)).toBe(true);
  await expect(page.locator('#assessment-narrate')).toHaveText('暂停');
  await page.locator('#assessment-narrate').click();
  expect(await page.locator('#narration').evaluate(a => a.paused)).toBe(true);
  for (const size of [{ width: 1280, height: 720 }, { width: 390, height: 600 }, { width: 320, height: 568 }]) {
    await page.setViewportSize(size);
    for (const id of ['assessment-practice', 'assessment-narrate', 'assessment-replay', 'assessment-next']) {
      await expect(page.locator('#' + id)).toBeInViewport({ ratio: 1 });
    }
  }
  await page.keyboard.down('Space');
  await expect(page.locator('#stop-record')).toBeEnabled();
  await page.keyboard.down('Space'); // Ignore held-key repeats.
  await expect(page.locator('#stop-record')).toBeEnabled();
  await page.keyboard.up('Space');
  await expect(page.locator('#assessment-details')).toBeHidden();
  await page.keyboard.press('Space');
  await expect(page.locator('#assess-recording')).toHaveText('85 分');
  expect(version).toBe(2);
  await page.locator('#assess-recording').click();
  await page.keyboard.press('ArrowRight');
  await expect(page.locator('#assessment-details')).toBeHidden();
  await expect(page.locator('#page-number')).toHaveText('第 3 / 3 页');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#assess-recording')).toHaveText('85 分');
  await page.locator('#assess-recording').click();
  await page.locator('#assessment-next').click();
  await expect(page.locator('#assessment-details')).toBeHidden();
  await expect(page.locator('#page-number')).toHaveText('第 3 / 3 页');
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#assess-recording')).toHaveText('85 分');
  await page.locator('#assess-recording').click();
  await page.keyboard.press('ArrowLeft');
  await expect(page.locator('#assessment-details')).toBeHidden();
  await expect(page.locator('#page-number')).toHaveText('第 1 / 3 页');
  expect(errors).toEqual([]);
});

for (const timezoneId of ['America/New_York', 'Asia/Shanghai', 'UTC']) {
  test.describe(timezoneId, () => {
    test.use({ timezoneId });
    test('morning completions survive device midnight and refresh, then roll over at Beijing midnight', async ({ page }) => {
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      // 07:50 Beijing, but still the previous evening in New York.
      await page.clock.install({ time: new Date('2026-10-21T23:50:00Z') });
      await page.route('**/api/library', async route => {
        const response = await route.fetch(), library = await response.json();
        let completed = 0;
        for (const book of library.books) {
          // Only original generated books; count them as imported books for this fixture.
          book.demo = false;
          const done = completed++ < 3;
          book.progress = { page: done ? book.page_count : 1, completed: done, visited: done };
          book.completion_days = done ? ['2026-10-22'] : [];
          book.acknowledged_writes = {};
        }
        await route.fulfill({ response, json: library });
      });
      await page.goto('/');
      await expect(page).toHaveTitle(/RAZ Reader/);
      await expect(page.locator('.book-card').first()).toBeVisible();
      const count = page.locator('#shelf-read-count');
      await expect(count).toHaveText('今日读完 3 本 · 累计 3 本');
      const snapshot = await page.evaluate(async () => {
        const { captureProgress } = await import('/progress.js');
        return captureProgress({ edition_id: 'timezone-test-only', page_count: 3,
          progress: { page: 2, completed: false } }, 3).body;
      });
      expect(snapshot.completion_days).toEqual(['2026-10-22']);
      expect(snapshot.read_at).toMatch(/^2026-10-21T23:50:0\d\.\d{3}Z$/);
      // New York has crossed midnight. Beijing is still on the same reading day.
      await page.clock.fastForward(5 * 60 * 60 * 1000);
      await expect(count).toHaveText('今日读完 3 本 · 累计 3 本');
      await page.reload();
      await expect(count).toHaveText('今日读完 3 本 · 累计 3 本');
      // Leave the shelf open through Beijing midnight; no click/reload needed.
      await page.clock.fastForward((11 * 60 + 11) * 60 * 1000);
      await expect(count).toHaveText('今日读完 0 本 · 累计 3 本');
      await expect(count).toHaveAttribute('title', '按北京时间统计，每天零点更新');
      expect(errors).toEqual([]);
    });
  });
}
