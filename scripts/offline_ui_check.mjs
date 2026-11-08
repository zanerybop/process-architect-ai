import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
import { authenticateSmokePage, smokeCredentials } from './browser_auth.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = path.join(root, 'artifacts/m3');
const credentials = smokeCredentials();
await mkdir(output, { recursive: true });
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}) });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const authentication = await authenticateSmokePage(page, process.env.UI_URL || 'http://127.0.0.1:5174', credentials, expect);
  await page.getByRole('button', { name: 'Генерация недоступна' }).click();
  await page.getByText('Не подключён worker. Можно открывать и редактировать BPMN-файлы.').waitFor();
  await page.getByRole('button', { name: 'Генерация недоступна' }).click();
  await page.getByTestId('import-file').setInputFiles(path.join(root, 'artifacts/m1/P10/generated.bpmn'));
  await page.getByTestId('mode').filter({ hasText: 'без генерации' }).waitFor();
  await page.locator('.djs-element[data-element-id="p10_register"]').click();
  await page.getByTestId('element-name').fill('Ручная правка в обычном приложении');
  await page.getByTestId('rename').click();
  assert.equal(await page.getByTestId('export').isDisabled(), true);
  await page.getByTestId('validate').click();
  await page.getByTestId('check-xsd').filter({ hasText: 'Пройдено' }).waitFor();
  const downloadPromise = page.waitForEvent('download');
  await page.getByTestId('export').click();
  const download = await downloadPromise;
  const saved = path.join(output, 'actual-ui-edited.bpmn');
  await download.saveAs(saved);
  const xml = await readFile(saved, 'utf8');
  assert.ok(xml.includes('Ручная правка в обычном приложении'));
  const dismissNotice = page.getByRole('button', { name: 'Закрыть уведомление' });
  if (await dismissNotice.count()) await dismissNotice.click();
  const poolBounds = () => page.evaluate(() => {
    const palette = document.querySelector('.djs-palette').getBoundingClientRect();
    const canvas = document.querySelector('[data-testid="modeler"]').getBoundingClientRect();
    return {
      palette_right: palette.right, canvas_right: canvas.right,
      pools: ['p10_customer_pool', 'p10_supplier_pool'].map(id => {
        const pool = document.querySelector(`.djs-element[data-element-id="${id}"] .djs-visual > rect`).getBoundingClientRect();
        return { id, left: pool.left, right: pool.right };
      }),
    };
  });
  await expect.poll(async () => {
    const bounds = await poolBounds();
    return Math.min(...bounds.pools.flatMap(pool =>
      [pool.left - bounds.palette_right, bounds.canvas_right - pool.right]));
  }, { message: 'Автоподгонка оставляет рамки пулов справа от палитры и внутри холста' }).toBeGreaterThanOrEqual(8);
  const autoFit = await poolBounds();
  await page.screenshot({ path: path.join(output, 'actual-ui.png'), fullPage: true });
  const report = {
    status: 'PASS', browser: await browser.version(), mode: 'imported_file',
    authentication,
    backend: 'real_application_no_test_runner', worker: 'unavailable',
    operations: ['file import', 'rename', 'actual API XML validation', 'current XML download'],
    inspector_auto_fit: autoFit,
    note: 'Проверка редактирования файла. Генерация, LLM и worker не подменялись и не запускались.',
  };
  await writeFile(path.join(output, 'actual-ui-report.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report));
} finally { await browser.close(); }
