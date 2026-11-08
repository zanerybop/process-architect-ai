// Actual application import/edit/export/reimport of saved outputs. No generation
// endpoint or model-produced Python is executed by this acceptance script.
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { authenticateSmokePage } from './browser_auth.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const input = path.resolve(root, process.env.BPMN_BROWSER_INPUT || 'artifacts/full-bpmn/reliability-v7/benchmark');
const output = path.resolve(root, process.env.BPMN_BROWSER_OUTPUT || 'artifacts/full-bpmn/reliability-v7/production-browser');
const url = process.env.UI_URL || 'http://127.0.0.1:8088';
const hash = value => createHash('sha256').update(value).digest('hex');
const relative = value => path.relative(root, value).split(path.sep).join('/');
// Reuse the exact authored bidirectional semantic comparator tested by the
// companion browser acceptance; this is trusted source, not generated code.
const runner = await readFile(path.join(root, 'scripts/document_benchmark_browser.mjs'), 'utf8');
const marker = 'result.semantic_preservation = await page.evaluate(';
const start = runner.indexOf(marker) + marker.length;
const end = runner.indexOf('}, { before: xml, after: edited.xml', start) + 1;
assert.ok(start > marker.length && end > start, 'Authored comparator callback not found');
const compare = runner.slice(start, end);
const semanticXsd = await readFile(path.join(root, 'schemas/bpmn/Semantic.xsd'), 'utf8');
const credentials = { username: `production_browser_${Date.now()}`, password: randomBytes(32).toString('base64url') };
const provision = spawnSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m',
  'backend.app.manage_users', 'create', credentials.username, '--password-stdin'], {
  cwd: root, input: `${credentials.password}\n`, encoding: 'utf8', windowsHide: true,
});
assert.equal(provision.status, 0, 'Local test account provisioning failed');
await mkdir(output, { recursive: true });
const report = { status: 'RUNNING', executed_at: new Date().toISOString(), ui_base_url: url,
  scope: 'Production App import, explicit rename, validation, export, reimport; semantic XML compared bidirectionally except explicit edit and documented defaults.',
  model_requests: 0, generation_requests: 0, input: relative(input), cases: [] };
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
const flush = () => writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
try {
  report.browser = browser.version();
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  await authenticateSmokePage(page, url, credentials, expect);
  // Fail closed if a later application change accidentally starts generation.
  await page.route('**/api/generate', route => { report.generation_requests++; return route.abort(); });
  for (const item of await readdir(input, { withFileTypes: true })) {
    if (!item.isDirectory() || process.env.BPMN_BROWSER_FILTER && !item.name.includes(process.env.BPMN_BROWSER_FILTER)) continue;
    const filename = path.join(input, item.name, 'generated.bpmn');
    const xml = await readFile(filename, 'utf8').catch(() => null);
    if (!xml) continue;
    const directory = path.join(output, item.name);
    await mkdir(directory, { recursive: true });
    const result = { id: item.name, status: 'FAIL', origin: process.env.BPMN_BROWSER_ORIGIN || 'current_benchmark_live_output',
      input: relative(filename), input_sha256: hash(xml), page_errors: [], console_errors: [] };
    report.cases.push(result);
    const onError = error => result.page_errors.push(String(error));
    const onConsole = message => { if (message.type() === 'error') result.console_errors.push(message.text()); };
    page.on('pageerror', onError); page.on('console', onConsole);
    try {
      await page.goto(url);
      result.ui_assets = await page.evaluate(() => [...document.scripts].map(script => script.src).filter(Boolean));
      await page.getByTestId('import-file').setInputFiles({ name: `${item.name}.bpmn`, mimeType: 'application/xml', buffer: Buffer.from(xml) });
      await expect(page.getByTestId('export')).toBeEnabled({ timeout: 15000 });
      const documentMode = await page.getByTestId('bpmn-document-editor').isVisible();
      result.renderer = documentMode ? 'production_document' : 'production_modeler';
      if (process.env.BPMN_EXPECT_RENDERER) assert.equal(result.renderer, process.env.BPMN_EXPECT_RENDERER);
      let desiredDiagram;
      if (documentMode && await page.getByTestId('document-diagram-select').count()) {
        desiredDiagram = await page.evaluate(text => {
          const doc = new DOMParser().parseFromString(text, 'application/xml');
          const elements = [...doc.querySelectorAll('[id]')];
          const editable = elements.find(el => /Task$|Activity$|Conversation$|^task$|^conversation$|^dataObject$/.test(el.localName)
            && [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')]
              .some(shape => shape.getAttribute('bpmnElement')?.split(':').at(-1) === el.id));
          const shape = [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')]
            .find(shape => shape.getAttribute('bpmnElement')?.split(':').at(-1) === editable?.id);
          const scope = shape?.parentElement?.getAttribute('bpmnElement')?.split(':').at(-1);
          return elements.find(el => el.id === scope)?.getAttribute('name');
        }, xml);
        if (desiredDiagram) {
          await page.getByTestId('document-diagram-select').click();
          await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: desiredDiagram }).click();
          await page.keyboard.press('Escape');
        }
      }
      const editable = await page.evaluate(({ xml, documentMode }) => {
        const parsed = new DOMParser().parseFromString(xml, 'application/xml');
        const candidates = [...parsed.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', '*')]
          .filter(el => /Task$|Activity$|Conversation$|^task$|^conversation$|^dataObject$/.test(el.localName))
          .filter(el => documentMode ? document.querySelector(`[data-testid="document-shape-${el.id}"]`)
            : document.querySelector(`.djs-element[data-element-id="${el.id}"]`));
        const selected = candidates[0];
        if (!selected) throw new Error('No editable visible semantic element');
        return { id: selected.id, name: selected.getAttribute('name') || '' };
      }, { xml, documentMode });
      const toggle = page.getByTestId('inspector-toggle');
      if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click();
      await page.screenshot({ path: path.join(directory, 'import-ui.png'), animations: 'disabled' });
      if (process.env.BPMN_CHECK_EVENT_DETAILS === '1') {
        const expected = await page.evaluate(text => {
          const doc = new DOMParser().parseFromString(text, 'application/xml');
          const all = [...doc.querySelectorAll('[id]')], ns = 'http://www.omg.org/spec/BPMN/20100524/MODEL';
          return all.filter(el => /Event$/.test(el.localName)).flatMap(event => [...event.children].flatMap(definition => {
            if (definition.namespaceURI !== ns) return [];
            if (definition.localName === 'messageEventDefinition') {
              const ref = definition.getAttribute('messageRef')?.split(':').at(-1);
              const name = all.find(el => el.id === ref)?.getAttribute('name');
              return name ? [{ id: event.id, kind: 'messages', value: name }] : [];
            }
            if (definition.localName === 'timerEventDefinition') return [...definition.children]
              .filter(el => ['timeDuration', 'timeDate', 'timeCycle'].includes(el.localName))
              .map(el => ({ id: event.id, kind: 'timers', value: el.textContent.trim() }));
            return [];
          }));
        }, xml);
        assert.ok(expected.length > 0, 'No named message or timer events found');
        result.event_details = [];
        for (const item of expected) {
          const node = documentMode ? page.getByTestId(`document-shape-${item.id}`)
            : page.locator(`.djs-element[data-element-id="${item.id}"] .djs-hit`);
          await node.click();
          await expect(page.getByTestId(`activity-${item.kind}`)).toContainText(item.value);
          await page.screenshot({ path: path.join(directory, `event-${item.id}.png`), animations: 'disabled' });
          result.event_details.push({ ...item, visible: true });
        }
      }
      const name = `${editable.name} — проверка экспорта`;
      if (documentMode) {
        await page.getByTestId(`document-shape-${editable.id}`).click();
        await page.getByTestId('document-attr-name').fill(name);
        await page.getByTestId('document-apply').click();
      } else {
        const outline = page.getByTestId('outline-select');
        await outline.click();
        await outline.locator('input').fill(editable.name || editable.id);
        await page.locator('.ant-select-dropdown:visible .ant-select-item-option').first().click();
        await page.getByTestId('element-name').fill(name);
        await page.getByTestId('rename').click();
      }
      await page.getByTestId('validate').click();
      await expect(page.getByTestId('export')).toBeEnabled({ timeout: 15000 });
      const pending = page.waitForEvent('download');
      await page.getByTestId('export').click();
      await (await pending).saveAs(path.join(directory, 'edited.bpmn'));
      const edited = await readFile(path.join(directory, 'edited.bpmn'), 'utf8');
      result.semantic_preservation = await page.evaluate(`(${compare})(${JSON.stringify({ before: xml, after: edited, editedId: editable.id, semanticXsd })})`);
      assert.equal(result.semantic_preservation.preserved, true, 'Unexpected semantic XML change');
      const currentName = await page.evaluate(({ xml, id }) => [...new DOMParser().parseFromString(xml, 'application/xml').querySelectorAll('[id]')]
        .find(el => el.getAttribute('id') === id)?.getAttribute('name'), { xml: edited, id: editable.id });
      assert.equal(currentName, name, 'Export did not include the current edited name');
      await page.getByTestId('import-file').setInputFiles({ name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(edited) });
      await expect(page.getByTestId('export')).toBeEnabled({ timeout: 15000 });
      const reimportDocument = await page.getByTestId('bpmn-document-editor').isVisible();
      if (reimportDocument && desiredDiagram) {
        await page.getByTestId('document-diagram-select').click();
        await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: desiredDiagram }).click();
        await page.keyboard.press('Escape');
      }
      if (reimportDocument) await expect(page.getByTestId(`document-shape-${editable.id}`)).toContainText(name);
      else await expect(page.locator(`.djs-element[data-element-id="${editable.id}"]`)).toContainText(name);
      if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click();
      await page.screenshot({ path: path.join(directory, 'reimport-ui.png'), animations: 'disabled' });
      assert.deepEqual(result.page_errors, []); assert.deepEqual(result.console_errors, []);
      result.edit = { id: editable.id, name };
      result.export_sha256 = hash(edited); result.reimport = 'PASS'; result.status = 'PASS';
    } catch (error) { result.error = String(error).replaceAll(credentials.password, '[REDACTED]'); }
    finally { page.off('pageerror', onError); page.off('console', onConsole); }
    result.input_unchanged = hash(await readFile(filename)) === result.input_sha256;
    if (!result.input_unchanged) { result.status = 'FAIL'; result.error = 'Input XML changed during acceptance'; }
    await flush(); console.log(JSON.stringify({ id: item.name, status: result.status, error: result.error }));
  }
  await context.close();
} finally { await browser.close(); }
report.total = report.cases.length; report.passed = report.cases.filter(item => item.status === 'PASS').length;
report.status = report.total && report.passed === report.total && report.generation_requests === 0 ? 'PASS' : 'FAIL';
await flush(); console.log(JSON.stringify({ status: report.status, passed: report.passed, total: report.total }));
process.exitCode = report.status === 'PASS' ? 0 : 1;
