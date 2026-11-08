// Actual bpmn-js Modeler editing of authored and saved live XML. No model requests.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium } = require('@playwright/test');
const output = path.join(root, 'artifacts/full-bpmn/modeler-browser');
const dist = path.join(root, 'frontend/node_modules/bpmn-js/dist');
const relative = file => path.relative(root, file).split(path.sep).join('/');
const hash = value => createHash('sha256').update(value).digest('hex');
const semanticXsd = await readFile(path.join(root, 'schemas/bpmn/Semantic.xsd'), 'utf8');
const cases = [];
for (const item of await readdir(path.join(root, 'artifacts/full-bpmn/fixtures'))) {
  if (/^F0[1-4]_/.test(item)) cases.push({ id: item, origin: 'authored_fixture_no_llm',
    input: path.join(root, 'artifacts/full-bpmn/fixtures', item, 'generated.bpmn') });
}
async function findLive(directory, prefix = 'live-nested', origin = 'saved_live_output') {
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, item.name);
    if (item.isDirectory()) await findLive(filename, prefix, origin);
    else if (item.name === 'generated.bpmn') cases.push({
      id: `${prefix}-${path.basename(directory)}`, origin, input: filename,
    });
  }
}
await findLive(path.join(root, 'artifacts/full-bpmn/live-nested'));
const compensationInputs = path.join(root, 'artifacts/full-bpmn/live-compensation');
if (await readdir(compensationInputs).then(() => true).catch(() => false)) {
  await findLive(compensationInputs, 'live-compensation');
}
const relayoutInputs = path.join(output, 'relayout-inputs');
if (await readdir(relayoutInputs).then(() => true).catch(() => false)) {
  await findLive(relayoutInputs, 'live-nested-relayout', 'saved_live_semantics_with_new_deterministic_di');
}
await mkdir(output, { recursive: true });
const report = { executed_at: new Date().toISOString(), status: 'RUNNING', model_requests: 0,
  scope: 'Chromium + bpmn-js Modeler: import, rename activity, move activity, export, reimport. All warnings retained.',
  limitations: ['Authored fixtures are not evidence of LLM generation quality.',
    'Import/edit checks do not prove execution semantics or human semantic acceptance.',
    'Choreography and conversation are deliberately excluded; their separate renderer needs separate checks.'],
  semantic_comparison: 'Original semantic elements, attributes and text are preserved, except the explicit name edit. Lane membership is unordered; omitted XSD defaults are equivalent. Raw first-run comparison is retained in initial-raw-comparison.json.',
  cases: [], bpmn_js_version: require('bpmn-js/package.json').version };
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
try {
  report.browser = await browser.version();
  for (const item of cases) {
    const directory = path.join(output, item.id);
    await mkdir(directory, { recursive: true });
    const xml = await readFile(item.input, 'utf8');
    const result = { id: item.id, origin: item.origin, input: relative(item.input),
      input_sha256: hash(xml), status: 'FAIL', console_errors: [], page_errors: [] };
    const metadata = await readFile(path.join(path.dirname(item.input), 'metadata.json'), 'utf8')
      .then(JSON.parse).catch(() => null);
    if (metadata) result.provenance = { mode: metadata.mode, provider: metadata.provider,
      model: metadata.model, prompt_version: metadata.prompt_version, prompt_sha256: metadata.prompt_sha256 };
    report.cases.push(result);
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
    page.on('pageerror', error => result.page_errors.push(String(error)));
    page.on('console', message => {
      if (message.type() === 'error') result.console_errors.push(message.text());
    });
    try {
      await page.setContent('<html lang="ru"><head><meta charset="utf-8"></head><body style="margin:0;background:#fff"><div id="canvas" style="width:100vw;height:100vh"></div></body></html>');
      for (const css of ['diagram-js.css', 'bpmn-js.css', 'bpmn-font/css/bpmn-embedded.css']) {
        await page.addStyleTag({ path: path.join(dist, 'assets', css) });
      }
      await page.addScriptTag({ path: path.join(dist, 'bpmn-modeler.production.min.js') });
      result.import = await page.evaluate(async text => {
        window.modeler = new window.BpmnJS({ container: '#canvas' });
        const imported = await window.modeler.importXML(text);
        await document.fonts.ready;
        const canvas = window.modeler.get('canvas');
        canvas.zoom('fit-viewport');
        const elements = window.modeler.get('elementRegistry').getAll();
        const boundaryPathsThroughHost = [];
        for (const edge of elements.filter(el => el.waypoints && el.source?.type === 'bpmn:BoundaryEvent')) {
          const host = edge.source.host;
          if (!host) continue;
          for (let index = 1; index < edge.waypoints.length; index++) {
            const a = edge.waypoints[index - 1], b = edge.waypoints[index];
            const vertical = a.x === b.x && a.x > host.x + 1 && a.x < host.x + host.width - 1
              && Math.max(a.y, b.y) > host.y + 1 && Math.min(a.y, b.y) < host.y + host.height - 1;
            const horizontal = a.y === b.y && a.y > host.y + 1 && a.y < host.y + host.height - 1
              && Math.max(a.x, b.x) > host.x + 1 && Math.min(a.x, b.x) < host.x + host.width - 1;
            if (vertical || horizontal) boundaryPathsThroughHost.push({ edge: edge.id, host: host.id, segment: index });
          }
        }
        const contains = (parent, child) => child.x >= parent.x - 1 && child.y >= parent.y - 1
          && child.x + child.width <= parent.x + parent.width + 1
          && child.y + child.height <= parent.y + parent.height + 1;
        const nested = elements.filter(el => !el.labelTarget && el.businessObject?.$parent
          && ['bpmn:SubProcess', 'bpmn:Transaction', 'bpmn:AdHocSubProcess'].includes(el.businessObject.$parent.$type)
          && !el.waypoints && el.type !== 'bpmn:BoundaryEvent').map(el => {
          const owner = window.modeler.get('elementRegistry').get(el.businessObject.$parent.id);
          return { id: el.id, parent: owner?.id, contained: !!owner && contains(owner, el) };
        });
        return { warnings: imported.warnings.map(w => ({ message: w.message, element: w.element?.id })),
          rendered_ids: elements.map(el => el.id), fit_zoom: canvas.zoom(), nested,
          boundary_paths_through_host: boundaryPathsThroughHost,
          subprocesses: elements.filter(el => !el.labelTarget
            && ['bpmn:SubProcess', 'bpmn:Transaction', 'bpmn:AdHocSubProcess'].includes(el.type))
            .map(el => ({ id: el.id, x: el.x, y: el.y, width: el.width, height: el.height })),
          svg: (await window.modeler.saveSVG()).svg };
      }, xml);
      await page.screenshot({ path: path.join(directory, 'import-fit.png') });
      await writeFile(path.join(directory, 'import.svg'), result.import.svg, 'utf8');
      delete result.import.svg;
      // Additional 1:1 view proves actual font readability; fit is not presented as readable text.
      const focus = result.import.subprocesses.slice().sort((a, b) => a.width - b.width)[0];
      if (focus) {
        await page.evaluate(box => {
          window.modeler.get('canvas').viewbox({ x: Math.max(0, box.x - 65), y: Math.max(0, box.y - 55),
            width: 1600, height: 1000 });
        }, focus);
        await page.screenshot({ path: path.join(directory, 'nested-100percent.png') });
      }
      const edited = await page.evaluate(async () => {
        const modeler = window.modeler;
        const registry = modeler.get('elementRegistry');
        const tasks = registry.getAll().filter(el => /Task$/.test(el.type) && !el.labelTarget);
        const task = tasks.find(el => ['bpmn:SubProcess', 'bpmn:Transaction', 'bpmn:AdHocSubProcess']
          .includes(el.businessObject.$parent?.$type)) ?? tasks[0];
        if (!task) throw new Error('No business activity to edit');
        const original = { id: task.id, name: task.businessObject.name, x: task.x, y: task.y,
          semantic_parent: task.businessObject.$parent.id,
          attachers: (task.attachers ?? []).map(el => ({ id: el.id, x: el.x, y: el.y })) };
        const name = `${original.name} — проверено`;
        const modeling = modeler.get('modeling');
        modeling.updateProperties(task, { name });
        // The high-level move command includes attached boundary events, as a UI drag does.
        modeling.moveElements([task], { x: 0, y: 12 }, task.parent);
        const expected = { name, x: original.x, y: original.y + 12 };
        const saved = (await modeler.saveXML({ format: true })).xml;
        const reopened = await modeler.importXML(saved);
        const after = registry.get(original.id);
        modeler.get('canvas').zoom('fit-viewport');
        return { original, expected, actual: { name: after.businessObject.name, x: after.x, y: after.y,
          semantic_parent: after.businessObject.$parent.id,
          attachers: (after.attachers ?? []).map(el => ({ id: el.id, x: el.x, y: el.y })) },
          warnings: reopened.warnings.map(w => ({ message: w.message, element: w.element?.id })), xml: saved };
      });
      await writeFile(path.join(directory, 'edited.bpmn'), edited.xml, 'utf8');
      result.edited_sha256 = hash(edited.xml);
      result.semantic_preservation = await page.evaluate(({ before, after, editedId, semanticXsd }) => {
        const namespace = 'http://www.omg.org/spec/BPMN/20100524/MODEL';
        const schema = new DOMParser().parseFromString(semanticXsd, 'application/xml');
        const types = new Map([...schema.getElementsByTagNameNS('http://www.w3.org/2001/XMLSchema', 'complexType')]
          .map(type => [type.getAttribute('name'), type]));
        function defaults(typeName) {
          const type = types.get(typeName);
          if (!type) return {};
          const extension = type.getElementsByTagNameNS('http://www.w3.org/2001/XMLSchema', 'extension')[0];
          const values = extension ? defaults(extension.getAttribute('base').split(':').at(-1)) : {};
          for (const attribute of type.getElementsByTagNameNS('http://www.w3.org/2001/XMLSchema', 'attribute')) {
            if (attribute.hasAttribute('default')) values[`:${attribute.getAttribute('name')}`] = attribute.getAttribute('default');
          }
          return values;
        }
        function snapshot(text) {
          const parsed = new DOMParser().parseFromString(text, 'application/xml');
          const items = new Map();
          function visit(el, key) {
            if (el.namespaceURI !== namespace) return;
            const attrs = Object.fromEntries([...el.attributes].filter(a => a.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
              .map(a => [`${a.namespaceURI ?? ''}:${a.localName}`, a.value]));
            if (el.getAttribute('id') === editedId) delete attrs[':name'];
            const directText = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
              .map(n => n.textContent).join('').trim();
            const typeName = el.getAttributeNS('http://www.w3.org/2001/XMLSchema-instance', 'type')?.split(':').at(-1)
              ?? `t${el.localName[0].toUpperCase()}${el.localName.slice(1)}`;
            items.set(key, { tag: el.localName, attrs, text: directText, defaults: defaults(typeName) });
            const counts = {};
            for (const child of el.children) {
              if (child.namespaceURI !== namespace) continue;
              const index = counts[child.localName] = (counts[child.localName] ?? 0) + 1;
              // Lane memberships are sets; bpmn-js moves the edited member to their end.
              const suffix = child.localName === 'flowNodeRef' ? `=${child.textContent.trim()}` : `[${index}]`;
              visit(child, child.id ? `#${child.id}` : `${key}/${child.localName}${suffix}`);
            }
          }
          visit(parsed.documentElement, '#definitions');
          return items;
        }
        const left = snapshot(before), right = snapshot(after), changes = [], normalizedDefaults = [];
        for (const [key, item] of left) {
          const next = right.get(key);
          if (!next) { changes.push({ key, kind: 'missing' }); continue; }
          if (item.tag !== next.tag || item.text !== next.text) changes.push({ key, kind: 'tag_or_text', before: item, after: next });
          for (const [name, value] of Object.entries(item.attrs)) {
            if (next.attrs[name] !== value) {
              if (next.attrs[name] === undefined && item.defaults[name] === value) {
                normalizedDefaults.push({ key, attribute: name, explicit_value: value, schema_default: item.defaults[name] });
              } else changes.push({ key, kind: 'attribute', name, before: value, after: next.attrs[name] });
            }
          }
        }
        return { preserved: changes.length === 0, changes, normalized_defaults: normalizedDefaults,
          original_elements: left.size, exported_elements: right.size };
      }, { before: xml, after: edited.xml, editedId: edited.original.id, semanticXsd });
      const { xml: ignored, ...editFacts } = edited;
      result.edit = editFacts;
      await page.screenshot({ path: path.join(directory, 'edited-fit.png') });
      assert.deepEqual(result.import.warnings, []);
      assert.deepEqual(result.edit.warnings, []);
      assert.ok(result.import.nested.every(item => item.contained), 'Nested node outside its expanded container');
      assert.equal(edited.actual.name, edited.expected.name);
      assert.equal(edited.actual.x, edited.expected.x);
      assert.equal(edited.actual.y, edited.expected.y);
      assert.equal(edited.actual.semantic_parent, edited.original.semantic_parent);
      assert.deepEqual(edited.actual.attachers, edited.original.attachers.map(el => ({ ...el, y: el.y + 12 })));
      assert.equal(result.semantic_preservation.preserved, true, JSON.stringify(result.semantic_preservation.changes));
      assert.deepEqual(result.page_errors, []);
      result.status = 'PASS';
    } catch (error) { result.error = String(error); }
    finally {
      await page.close();
      result.input_unchanged = hash(await readFile(item.input)) === result.input_sha256;
      if (!result.input_unchanged) { result.status = 'FAIL'; result.error = 'Source XML changed during check'; }
    }
    await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
    console.log(JSON.stringify({ id: item.id, status: result.status, error: result.error }));
  }
} finally { await browser.close(); }
report.total = report.cases.length;
report.passed = report.cases.filter(item => item.status === 'PASS').length;
report.status = report.total && report.passed === report.total ? 'PASS' : 'FAIL';
report.geometry_findings = report.cases.flatMap(item => (item.import?.boundary_paths_through_host ?? [])
  .map(finding => ({ case: item.id, origin: item.origin, ...finding })));
report.geometry_status = report.geometry_findings.length ? 'findings' : 'passed';
await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
console.log(JSON.stringify({ status: report.status, passed: report.passed, total: report.total }));
process.exitCode = report.status === 'PASS' ? 0 : 1;
