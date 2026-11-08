import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium } = require('@playwright/test');
const { BpmnModdle } = require('bpmn-moddle');
const output = path.join(root, 'artifacts/public-editor');
await mkdir(output, { recursive: true });
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const report = { executed_at: new Date().toISOString(), url: 'https://demo.bpmn.io/new',
  command: 'node scripts/public_editor_check.mjs',
  data: 'Только синтетические P05/P10, созданные этим проектом.', cases: [], status: 'NOT_RUN' };
let browser;
try {
  browser = await chromium.launch({ ...(executablePath ? { executablePath } : {}), headless: true });
  report.browser = await browser.version();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  const consoleErrors = [];
  const pageErrors = [];
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('dialog', (dialog) => dialog.accept());
  await page.goto(report.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.locator('.djs-container').waitFor({ timeout: 30000 });
  report.title = await page.title();
  report.final_url = page.url();
  report.application_script = 'https://demo.bpmn.io/bpmn/app.js';
  const scriptResponse = await page.request.get(report.application_script);
  report.application_script_sha256 = createHash('sha256').update(await scriptResponse.body()).digest('hex');
  for (const id of ['P05', 'P10']) {
    const inputPath = path.join(root, `artifacts/m1/${id}/generated.bpmn`);
    const original = await readFile(inputPath, 'utf8');
    const source = (await new BpmnModdle().fromXML(original)).rootElement;
    const processes = source.rootElements.filter((item) => item.$type === 'bpmn:Process');
    const collaboration = source.rootElements.find((item) => item.$type === 'bpmn:Collaboration');
    const expected = [
      ...processes.flatMap((item) => item.flowElements ?? []),
      ...processes.flatMap((item) => (item.laneSets ?? []).flatMap((set) => set.lanes)),
      ...(collaboration?.participants ?? []), ...(collaboration?.messageFlows ?? []),
    ];
    const task = expected.find((item) => ['bpmn:Task', 'bpmn:UserTask', 'bpmn:SendTask'].includes(item.$type));
    const entry = { id, input: path.relative(root, inputPath).replaceAll('\\', '/'),
      input_sha256: createHash('sha256').update(original).digest('hex'), expected_elements: expected.length };
    report.cases.push(entry);
    await page.locator('input[type=file]').setInputFiles(inputPath);
    await page.locator(`.djs-element[data-element-id="${task.id}"]`).waitFor({ timeout: 30000 });
    await page.getByTitle('reset zoom', { exact: true }).click();
    await page.waitForFunction((expectedIds) => expectedIds.every((elementId) =>
      document.querySelector(`.djs-element[data-element-id="${elementId}"]`)), expected.map((item) => item.id));
    entry.import_error_visible = await page.locator('.screen.error').isVisible();
    entry.import_warning_visible = await page.locator('[jswidget="import-warnings-alert"]').isVisible();
    entry.import_warnings = await page.locator('[jswidget="import-warnings-dialog"] textarea').inputValue();
    assert.equal(entry.import_error_visible, false);
    assert.equal(entry.import_warning_visible, false);
    const renderedIds = await page.locator('.djs-element').evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-element-id')));
    entry.rendered_elements = renderedIds.filter((elementId) => expected.some((item) => item.id === elementId)).length;
    entry.rendered_external_labels = renderedIds.filter((elementId) => elementId.endsWith('_label')).length;
    assert.equal(entry.rendered_elements, expected.length);
    await page.screenshot({ path: path.join(output, `${id}-import.png`) });

    const editedName = `${task.name} — внешняя проверка`;
    await page.locator(`.djs-element[data-element-id="${task.id}"]`).dblclick();
    await page.locator('.djs-direct-editing-content').fill(editedName);
    await page.locator('.djs-direct-editing-content').press('Control+Enter');
    await page.locator('.djs-direct-editing-content').waitFor({ state: 'hidden' });
    const downloadPromise = page.waitForEvent('download');
    await page.getByTitle('Download as BPMN 2.0 file', { exact: true }).click();
    const download = await downloadPromise;
    const downloadedPath = path.join(output, `${id}-public-edited.bpmn`);
    await download.saveAs(downloadedPath);
    const downloaded = await readFile(downloadedPath, 'utf8');
    const parsedDownload = await new BpmnModdle().fromXML(downloaded);
    const downloadedTask = parsedDownload.rootElement.rootElements
      .flatMap((item) => item.flowElements ?? []).find((item) => item.id === task.id);
    assert.equal(downloadedTask.name, editedName);
    entry.edited_element_id = task.id;
    entry.edited_name = editedName;
    entry.exporter = parsedDownload.rootElement.exporter;
    entry.exporter_version = parsedDownload.rootElement.exporterVersion;
    entry.download_parse_warnings = parsedDownload.warnings.map((warning) => warning.message);
    entry.download_sha256 = createHash('sha256').update(downloaded).digest('hex');
    entry.export = path.relative(root, downloadedPath).replaceAll('\\', '/');
    const previousShape = await page.locator(`.djs-element[data-element-id="${task.id}"]`).elementHandle();
    await page.locator('input[type=file]').setInputFiles(downloadedPath);
    await page.waitForFunction((element) => !element.isConnected, previousShape);
    await page.waitForFunction(({ elementId, name }) =>
      document.querySelector(`.djs-element[data-element-id="${elementId}"]`)?.textContent.replace(/\s/g, '')
        .includes(name.replace(/\s/g, '')),
    { elementId: task.id, name: editedName });
    await page.getByTitle('reset zoom', { exact: true }).click();
    assert.equal(await page.locator('[jswidget="import-warnings-alert"]').isVisible(), false);
    await page.screenshot({ path: path.join(output, `${id}-reimport.png`) });
    entry.status = 'PASS';
  }
  report.console_errors = consoleErrors;
  report.page_errors = pageErrors;
  report.status = report.cases.every((entry) => entry.status === 'PASS') ? 'PASS' : 'FAIL';
} catch (error) {
  report.status = error.name === 'AssertionError' ? 'FAIL' : 'BLOCKED';
  report.error = error.message;
} finally {
  await browser?.close();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n', 'utf8');
}
console.log(JSON.stringify(report, null, 2));
if (report.status !== 'PASS') process.exitCode = 1;
