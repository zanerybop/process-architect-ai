import { defineConfig } from '@playwright/test';
import { mkdirSync, mkdtempSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { tmpdir } from 'node:os';

// Inherited by test workers and the fixture server; never points at production data.
if (!process.env.BROWSER_TEST_DATA_DIR) {
  // Keep storage paths short enough for atomic revision writes on Windows.
  const prefix = join(tmpdir(), 'bpmn-browser-');
  mkdirSync(dirname(prefix), { recursive: true });
  process.env.BROWSER_TEST_DATA_DIR = mkdtempSync(prefix);
}

export default defineConfig({
  // The fixture API intentionally retains the production generation capacity.
  workers: 1,
  testDir: './tests/browser',
  outputDir: './test-results',
  reporter: [['list'], ['json', { outputFile: '../artifacts/browser/results.json' }]],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    viewport: { width: 1440, height: 900 },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {},
  },
  webServer: [
    {
      command: 'uv run --directory .. python -m tests.browser_server',
      url: 'http://127.0.0.1:8010/api/health', reuseExistingServer: false,
      env: { BROWSER_TEST_DATA_DIR: process.env.BROWSER_TEST_DATA_DIR },
    },
    { command: 'npm run dev -- --port 5173 --strictPort', url: 'http://127.0.0.1:5173', reuseExistingServer: false, env: { BPMN_API_PROXY: 'http://127.0.0.1:8010' } },
  ],
});
