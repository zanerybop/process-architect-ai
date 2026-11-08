// Browser acceptance of each saved live XML; no LLM calls and no fixture substitutions.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium } = require('@playwright/test');
const input = path.resolve(root, process.argv[2] ?? 'artifacts/quality/benchmark/run-01');
const output = path.resolve(root, process.argv[3] ?? 'artifacts/quality/benchmark/browser-01');
await mkdir(output, { recursive: true });
const reportPath = path.join(output, 'report.json');
const previous = await readFile(reportPath, 'utf8').then(JSON.parse).catch(() => ({ cases: [] }));
const report = { executed_at: new Date().toISOString(), input: path.relative(root, input),
  scope: 'Real Chromium bpmn-js import, rename, export and reimport of saved live outputs. Not semantic acceptance.',
  cases: previous.cases, status: 'RUNNING' };
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
try {
  report.browser = await browser.version();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await page.setContent('<html lang="ru"><head><meta charset="utf-8"></head><body style="margin:0"><div id="canvas" style="height:100vh;width:100vw"></div></body></html>');
  await page.addStyleTag({ path: path.join(root, 'frontend/node_modules/bpmn-js/dist/assets/diagram-js.css') });
  await page.addStyleTag({ path: path.join(root, 'frontend/node_modules/bpmn-js/dist/assets/bpmn-js.css') });
  await page.addScriptTag({ path: path.join(root, 'frontend/node_modules/bpmn-js/dist/bpmn-modeler.production.min.js') });
  await page.evaluate(() => { window.modeler = new window.BpmnJS({ container: '#canvas' }); });
  for (const entry of await readdir(input, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const directory = path.join(input, entry.name);
    const complete = await readFile(path.join(directory, 'complete.json'), 'utf8').then(JSON.parse).catch(() => null);
    if (!complete) continue;
    const xml = await readFile(path.join(directory, 'generated.bpmn'), 'utf8').catch(() => null);
    if (!xml) continue;
    const sha = createHash('sha256').update(xml).digest('hex');
    if (report.cases.some(item => item.run === entry.name && item.input_sha256 === sha)) continue;
    const result = { run: entry.name, input_sha256: sha, status: 'FAIL' };
    report.cases.push(result);
    try {
      const inspected = await page.evaluate(async text => {
        const modeler = window.modeler;
        const imported = await modeler.importXML(text);
        modeler.get('canvas').zoom('fit-viewport');
        const registry = modeler.get('elementRegistry');
        const node = registry.getAll().find(el => /Task$/.test(el.type));
        if (!node) throw new Error('No business activity available to edit');
        const originalName = node.businessObject.name;
        const changed = `${originalName} — проверка & <экспорт>`;
        modeler.get('modeling').updateProperties(node, { name: changed });
        const saved = (await modeler.saveXML({ format: true })).xml;
        const reopened = await modeler.importXML(saved);
        modeler.get('canvas').zoom('fit-viewport');
        return { warnings: imported.warnings.map(item => item.message),
          reimport_warnings: reopened.warnings.map(item => item.message),
          id: node.id, name: registry.get(node.id).businessObject.name, expected_name: changed,
          rendered_elements: registry.getAll().length, xml: saved };
      }, xml);
      assert.deepEqual(inspected.warnings, []);
      assert.deepEqual(inspected.reimport_warnings, []);
      assert.equal(inspected.name, inspected.expected_name);
      await writeFile(path.join(output, `${entry.name}-edited.bpmn`), inspected.xml, 'utf8');
      if (entry.name.endsWith('-1')) await page.screenshot({ path: path.join(output, `${entry.name}.png`) });
      const { xml: ignored, ...facts } = inspected;
      Object.assign(result, facts, { status: 'PASS' });
    } catch (error) { result.error = String(error).slice(0, 1000); }
    await writeFile(reportPath, JSON.stringify(report, null, 2), 'utf8');
    console.log(JSON.stringify({ run: entry.name, status: result.status }));
  }
} finally { await browser.close(); }
report.status = report.cases.length && report.cases.every(item => item.status === 'PASS') ? 'PASS' : 'FAIL';
report.passed = report.cases.filter(item => item.status === 'PASS').length;
report.total = report.cases.length;
await writeFile(reportPath, JSON.stringify(report, null, 2), 'utf8');
console.log(JSON.stringify({ status: report.status, passed: report.passed, total: report.total }));
process.exitCode = report.status === 'PASS' ? 0 : 1;
