// Real production UI/API/LLM/worker check. No route interception or fixture runner.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { authenticateSmokePage, smokeCredentials } from './browser_auth.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = path.resolve(root, process.env.SMOKE_OUTPUT_DIR || 'artifacts/strata/live-ui');
const url = process.env.UI_URL || 'http://127.0.0.1:8088';
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const description = process.env.SMOKE_SOURCE_FILE
  ? (await readFile(path.resolve(root, process.env.SMOKE_SOURCE_FILE), 'utf8')).trim()
  : 'Техник получает заявку на поверку датчика, регистрирует её в журнале. '
  + 'После этого метролог проверяет данные прибора. Если данные полные, метролог '
  + 'назначает дату поверки и завершает процесс. Если данные неполные, техник '
  + 'запрашивает уточнения и возвращается к регистрации заявки.';
const editedName = 'Регистрация проверена: датчик & журнал <2026>';
const browserErrors = [];
const networkErrors = [];
const report = {
  status: 'RUNNING', started_at: new Date().toISOString(), url,
  test_transport: 'production_live', route_interception: false,
  description, operations: [], browser_errors: browserErrors, network_errors: networkErrors,
  semantic_review: 'not_run',
};
let browser;
let page;

async function saveJson(filename, value) {
  await writeFile(path.join(output, filename), JSON.stringify(value, null, 2), 'utf8');
}

async function setInspector(open) {
  const toggle = page.getByTestId('inspector-toggle');
  if (await toggle.getAttribute('aria-expanded') !== String(open)) await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', String(open));
}

function apiResponse(endpoint, method) {
  return page.waitForResponse(response => new URL(response.url()).pathname === endpoint
    && response.request().method() === method, { timeout: endpoint === '/api/generate' ? 400_000 : 30_000 });
}

async function xmlNodeName(xml, id) {
  return page.evaluate(({ value, elementId }) => {
    const document = new DOMParser().parseFromString(value, 'application/xml');
    if (document.querySelector('parsererror')) throw new Error('Invalid exported XML');
    return Array.from(document.querySelectorAll('[id]'))
      .find(element => element.getAttribute('id') === elementId)?.getAttribute('name');
  }, { value: xml, elementId: id });
}

await mkdir(output, { recursive: true });
try {
  const credentials = smokeCredentials();
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}) });
  report.browser = await browser.version();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 }, acceptDownloads: true,
  });
  context.setDefaultTimeout(30_000);
  page = await context.newPage();
  page.on('pageerror', error => browserErrors.push({ type: 'pageerror', message: error.message }));
  page.on('console', message => {
    if (message.type() === 'error') browserErrors.push({ type: 'console', message: message.text() });
  });
  page.on('requestfailed', request => networkErrors.push({
    url: request.url(), method: request.method(), error: request.failure()?.errorText,
  }));
  page.on('response', response => {
    if (response.status() >= 400) networkErrors.push({ url: response.url(), status: response.status() });
  });
  const healthResponse = await context.request.get(new URL('/api/health', url).href);
  assert.equal(healthResponse.status(), 200, 'Production API health must respond');
  report.health = await healthResponse.json();
  await saveJson('health.json', report.health);
  assert.equal(report.health.mode, 'live');
  assert.equal(report.health.dependencies.worker, true);
  assert.equal(report.health.dependencies.xsd_files_available, true);
  assert.equal(report.health.dependencies.llm_configured, true);
  assert.equal(report.health.generation_available, true);
  report.operations.push('production live health with worker');

  report.authentication = await authenticateSmokePage(page, url, credentials, expect);
  report.operations.push('local account authentication through UI');
  await expect(page.getByTestId('request-mode')).toContainText(/live/i);
  await page.getByTestId('description').fill(description);
  await expect(page.getByTestId('generate')).toBeEnabled();
  const [generationResponse] = await Promise.all([
    apiResponse('/api/generate', 'POST'), page.getByTestId('generate').click(),
  ]);
  report.generation_http_status = generationResponse.status();
  const generation = await generationResponse.json();
  await saveJson('generation.json', generation);
  if (generation.python_code) await writeFile(path.join(output, 'generated.py'), generation.python_code, 'utf8');
  if (generation.ir) await saveJson('ir.json', generation.ir);
  if (generation.bpmn_xml) await writeFile(path.join(output, 'generated.bpmn'), generation.bpmn_xml, 'utf8');
  assert.equal(generationResponse.status(), 200);
  assert.equal(generation.mode, 'live');
  assert.equal(generation.status, 'ready', JSON.stringify(generation.issues));
  assert.ok(generation.python_code?.trim(), 'Live Python artifact is required');
  assert.ok(generation.ir?.document?.elements?.length || generation.ir?.nodes?.length,
    'Worker-produced IR is required');
  assert.equal(generation.ir.source_text, description);
  assert.ok(generation.bpmn_xml?.includes('BPMNDiagram'), 'BPMN DI is required');
  for (const name of ['code', 'graph', 'xsd', 'layout']) assert.equal(generation.checks[name], 'passed', name);
  assert.equal(generation.checks.import, 'not_run', 'API must not claim browser import');
  report.generation_id = generation.id;
  report.attempt_count = generation.attempt_count;
  report.timings = generation.timings;
  report.generation_checks = generation.checks;
  report.operations.push('UI description → actual LLM Python → isolated worker IR → XML/DI');

  const task = generation.ir.document
    ? generation.ir.document.elements.filter(node => /task$/i.test(node.tag))
      .map(node => ({ id: node.attributes.id, name: node.attributes.name }))[0]
    : generation.ir.nodes.find(node => /task$/i.test(node.type));
  assert.ok(task, 'At least one generated task is required for editing');
  report.edited_element_id = task.id;
  report.original_name = task.name;
  report.edited_name = editedName;
  const taskElement = () => page.locator(`.djs-element[data-element-id="${task.id}"]`).first();
  await expect(taskElement()).toBeVisible();
  await setInspector(true);
  await expect(page.getByTestId('check-import')).toHaveText('Пройдено');
  await expect(page.getByTestId('export')).toBeEnabled();
  await setInspector(false);
  await page.getByRole('button', { name: 'Вписать', exact: true }).click();
  await page.screenshot({ path: path.join(output, 'generated.png'), fullPage: true });
  report.operations.push('actual bpmn-js import');

  await taskElement().click();
  await page.getByTestId('element-name').fill(editedName);
  await page.getByTestId('rename').click();
  await expect(page.getByTestId('export')).toBeDisabled();
  const [validationResponse] = await Promise.all([
    apiResponse('/api/review', 'POST'), page.getByTestId('validate').click(),
  ]);
  assert.equal(validationResponse.status(), 200);
  const validation = await validationResponse.json();
  await saveJson('validation.json', validation);
  for (const name of ['graph', 'xsd', 'layout']) assert.equal(validation.checks[name], 'passed', name);
  await expect(page.getByTestId('check-import')).toHaveText('Пройдено');
  await expect(page.getByTestId('audit-stale')).toBeVisible();
  await expect(page.getByTestId('export')).toBeEnabled();
  report.operations.push('manual task rename, current XML validation, stale audit');

  const [download, revisionResponse] = await Promise.all([
    page.waitForEvent('download'),
    apiResponse(`/api/generations/${generation.id}/revisions`, 'PUT'),
    page.getByTestId('export').click(),
  ]);
  assert.equal(revisionResponse.status(), 200);
  const revision = await revisionResponse.json();
  await saveJson('revision.json', revision);
  const exportedPath = path.join(output, 'edited.bpmn');
  await download.saveAs(exportedPath);
  const xml = await readFile(exportedPath, 'utf8');
  assert.equal(await xmlNodeName(xml, task.id), editedName);
  assert.notEqual(xml, generation.bpmn_xml, 'Export must contain current editor XML');
  const storedResponse = await context.request.get(new URL(`/api/generations/${generation.id}`, url).href);
  assert.equal(storedResponse.status(), 200);
  const stored = await storedResponse.json();
  assert.equal(stored.bpmn_xml, xml, 'Stored revision must match exact exported XML');
  assert.notEqual(stored.revision_id, generation.revision_id);
  assert.equal(stored.audit_stale, true);
  report.revision_id = stored.revision_id;
  report.operations.push('current XML download and persisted API revision');
  await page.screenshot({ path: path.join(output, 'edited.png'), fullPage: true });

  await page.getByTestId('import-file').setInputFiles(exportedPath);
  await expect(page.getByTestId('status')).toHaveText('BPMN-файл открыт');
  await expect(page.getByTestId('check-import')).toHaveText('Пройдено');
  await setInspector(false);
  await page.getByRole('button', { name: 'Вписать', exact: true }).click();
  await taskElement().click();
  await expect(page.getByTestId('element-name')).toHaveValue(editedName);
  await expect(page.getByTestId('export')).toBeEnabled();
  await expect(page.getByTestId('error')).toHaveCount(0);
  await page.screenshot({ path: path.join(output, 'reimported.png'), fullPage: true });
  report.operations.push('downloaded BPMN reimport preserving manually edited name');
  assert.deepEqual(browserErrors, [], 'No browser runtime or console errors');
  assert.deepEqual(networkErrors, [], 'No failed browser requests');
  report.status = 'PASS';
} catch (error) {
  report.status = 'FAIL';
  report.error = { name: error.name, message: error.message, stack: error.stack };
  if (page) await page.screenshot({ path: path.join(output, 'failure.png'), fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  report.finished_at = new Date().toISOString();
  await saveJson('report.json', report);
  await browser?.close();
  console.log(JSON.stringify(report, null, 2));
}
