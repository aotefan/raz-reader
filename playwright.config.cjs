const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests', testMatch: 'public-demo.spec.cjs', workers: 1, timeout: 60000,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:8876', headless: true,
    ...(process.env.PW_CHANNEL ? { channel: process.env.PW_CHANNEL } : {}),
    viewport: { width: 1365, height: 900 } },
  webServer: {
    command: '.venv/bin/python scripts/prepare_demo.py --data data/e2e && python3 -m engine.server --data data/e2e --port 8876',
    url: 'http://127.0.0.1:8876', reuseExistingServer: false,
  },
});
