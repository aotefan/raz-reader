const { test, expect } = require('@playwright/test');
const path = require('node:path');
const fs = require('node:fs');

// Browser plugin not available: use Playwright against real local application routes.
// Only original, locally generated example books may appear in these screenshots.
const screenshots = process.env.SCREENSHOT_DIR;
async function capture(page, name) {
  if (!screenshots) return;
  fs.mkdirSync(screenshots, { recursive: true });
  await page.waitForTimeout(350);
  await page.screenshot({ path: path.join(screenshots, name + '.png'), fullPage: false, animations: 'disabled' });
}

for (const mobile of [false, true]) {
  test(`original demo: shelf, search, reader, progress and picker (${mobile ? 'mobile' : 'desktop'})`, async ({ page, request }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.setViewportSize(mobile ? { width: 390, height: 844 } : { width: 1365, height: 900 });
    const library = await (await request.get('/api/library')).json();
    expect(library.books).toHaveLength(6);
    expect(library.books.every(book => book.demo)).toBe(true);
    const garden = library.books.find(book => book.title === 'A Little Garden');
    await request.post('/api/progress', { data: { edition_id: garden.edition_id, page: 1 } });
    await page.goto('/');
    await expect(page).toHaveTitle(/RAZ Reader/);
    await expect(page.locator('.book-card')).toHaveCount(6);
    await expect(page.locator('.cover-loading:visible')).toHaveCount(0, { timeout: 30000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await capture(page, mobile ? 'bookshelf-mobile' : 'bookshelf-desktop');
    await page.locator('#search').fill('Boat');
    await expect(page.locator('.book-card')).toHaveCount(1);
    await page.locator('#search').fill('');
    await page.getByRole('button', { name: /打开 A Little Garden/ }).click();
    await expect(page.locator('#next')).toBeEnabled();
    await page.locator('#next').click();
    await expect(page.locator('#page-number')).toHaveText('第 2 / 3 页');
    await expect.poll(async () => page.frameLocator('#pdf-frame').locator('.page[data-page-number="2"] canvas').count()).toBeGreaterThan(0);
    await expect(page.locator('#saved')).toContainText('已保存');
    await page.waitForTimeout(500);
    await capture(page, mobile ? 'reader-mobile' : 'reader-desktop');
    await page.locator('#back').click();
    await page.reload();
    await page.getByRole('button', { name: /打开 A Little Garden/ }).click();
    await expect(page.locator('#page-number')).toHaveText('第 2 / 3 页');
    await page.locator('#back').click();
    await page.locator('#level-select').click();
    await expect(page.locator('#selection-sheet')).toBeVisible();
    if (mobile) await capture(page, 'level-picker-mobile');
    await page.locator('#close-selection-sheet').click();
    await expect(page.locator('#selection-sheet')).not.toBeVisible();
    expect(errors).toEqual([]);
  });
}
