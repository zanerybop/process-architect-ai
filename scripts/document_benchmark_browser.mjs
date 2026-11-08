// Actual browser checks of saved live and benchmark XML. This script never calls an LLM.
// A random local test account is provisioned solely for production document-editor checks.
import assert from 'node:assert/strict';
import { createHash, randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { authenticateSmokePage } from './browser_auth.mjs';

const root = process.env.BPMN_WORKSPACE_ROOT
  ? path.resolve(process.env.BPMN_WORKSPACE_ROOT) : fileURLToPath(new URL('../', import.meta.url));
const require = createRequire(path.join(root, 'frontend/package.json'));
const { chromium, expect } = require('@playwright/test');
const output = path.resolve(root, process.env.BPMN_BROWSER_OUTPUT || 'artifacts/full-bpmn/visual-current');
const dist = path.join(root, 'frontend/node_modules/bpmn-js/dist');
const relative = file => path.relative(root, file).split(path.sep).join('/');
const hash = value => createHash('sha256').update(value).digest('hex');
const semanticXsd = await readFile(path.join(root, 'schemas/bpmn/Semantic.xsd'), 'utf8');
const cases = [];
async function findLive(directory, prefix, origin = 'saved_live_output') {
  if (!await readdir(directory).then(() => true).catch(() => false)) return;
  for (const item of await readdir(directory, { withFileTypes: true })) {
    const filename = path.join(directory, item.name);
    if (item.isDirectory()) await findLive(filename, prefix, origin);
    else if (item.name === 'generated.bpmn' && (origin !== 'current_benchmark_live_output'
      || await readFile(path.join(directory, 'complete.json')).then(() => true).catch(() => false))) cases.push({
      id: `${prefix}-${path.basename(directory)}`, origin, input: filename,
    });
  }
}
await findLive(path.join(root, 'artifacts/full-bpmn/live-nested'), 'live-nested');
const compensationInputs = path.join(root, 'artifacts/full-bpmn/live-compensation');
if (await readdir(compensationInputs).then(() => true).catch(() => false)) {
  await findLive(compensationInputs, 'live-compensation');
}
const relayoutInputs = path.join(root, 'artifacts/full-bpmn/modeler-browser/relayout-inputs');
if (await readdir(relayoutInputs).then(() => true).catch(() => false)) {
  await findLive(relayoutInputs, 'live-nested-relayout', 'saved_live_semantics_with_new_deterministic_di');
}
await findLive(path.join(root, 'artifacts/full-bpmn/live-choreography'), 'live-choreography');
await findLive(path.join(root, 'artifacts/full-bpmn/benchmark'), 'benchmark', 'current_benchmark_live_output');
if (!cases.some(item => /conversation/.test(item.input))) cases.push({ id: 'F06_conversations',
  origin: 'authored_fixture_no_llm', input: path.join(root, 'artifacts/full-bpmn/fixtures/F06_conversations/generated.bpmn') });
if (process.env.BPMN_BROWSER_INPUT) {
  cases.length = 0;
  await findLive(path.resolve(root, process.env.BPMN_BROWSER_INPUT), process.env.BPMN_BROWSER_PREFIX ?? 'replay',
    process.env.BPMN_BROWSER_ORIGIN ?? 'saved_model_output_recompiled');
  if (process.env.BPMN_BROWSER_INCLUDE_FIXTURE === '1') cases.push({ id: 'F06_conversations',
    origin: 'authored_fixture_no_llm', input: path.join(root, 'artifacts/full-bpmn/fixtures/F06_conversations/generated.bpmn') });
}
const selected = process.env.BPMN_BROWSER_FILTER ? cases.filter(item => item.input.includes(process.env.BPMN_BROWSER_FILTER)) : cases;
await mkdir(output, { recursive: true });
const report = { executed_at: new Date().toISOString(), status: 'RUNNING', model_requests: 0,
  ui_base_url: process.env.UI_URL || 'http://127.0.0.1:8088',
  scope: 'Chromium + bpmn-js Modeler and production full-document renderer: import, rename, move, export, reimport. No new LLM requests.',
  limitations: ['Authored fixtures are not evidence of LLM generation quality.',
    'Import/edit checks do not prove execution semantics or human semantic acceptance.',
    'Readability and geometric findings are independent of import and semantic preservation checks.'],
  semantic_comparison: 'Bidirectional semantic comparison except the explicit name edit. Lane membership is unordered; omitted or explicit XSD defaults are equivalent. CompensateEventDefinition.waitForCompletion true/1 is a separate normative BPMN default (Table 10.94, p.263), not an XSD default. Vendor/metadata content is protected. A moved attached boundary may gain the explicit lane membership already held by its original host; such additions are separately recorded and no other additions are allowed.',
  cases: [], bpmn_js_version: require('bpmn-js/package.json').version };
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE });
async function renderSavedSvg(svg, directory) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  try {
    await page.setContent('<html><head><meta charset="utf-8"></head><body style="margin:0;background:white"></body></html>');
    await page.evaluate(value => {
      const node = new DOMParser().parseFromString(value, 'image/svg+xml').documentElement;
      node.style.width = '1560px'; node.style.height = '960px'; node.style.margin = '20px';
      node.setAttribute('preserveAspectRatio', 'xMidYMid meet'); document.body.append(node);
    }, svg);
    await page.screenshot({ path: path.join(directory, 'diagram-overview.png') });
  } finally { await page.close(); }
}
try {
  report.browser = await browser.version();
  // Probe actual importer capability, not just notation-family names. Import
  // warnings or omitted DI must exercise the application's lossless fallback.
  const probe = await browser.newPage();
  try {
    await probe.setContent('<div id="capability-canvas" style="width:1200px;height:800px"></div>');
    await probe.addScriptTag({ path: path.join(dist, 'bpmn-modeler.production.min.js') });
    for (const item of selected) {
      item.capability = await probe.evaluate(async text => {
        const parsed = new DOMParser().parseFromString(text, 'application/xml');
        const bpmn = 'http://www.omg.org/spec/BPMN/20100524/MODEL';
        const di = 'http://www.omg.org/spec/BPMN/20100524/DI';
        const semantic = [...parsed.getElementsByTagNameNS(bpmn, '*')];
        if (semantic.some(el => ['choreography', 'conversation', 'subConversation', 'callConversation'].includes(el.localName))) {
          return { mode: 'document', reason: 'notation_requires_document_editor' };
        }
        const modeler = new window.BpmnJS({ container: '#capability-canvas' });
        try {
          const imported = await modeler.importXML(text);
          const registry = modeler.get('elementRegistry');
          const activePlane = modeler.get('canvas').getRootElement()?.businessObject?.id;
          const plane = [...parsed.getElementsByTagNameNS(di, 'BPMNPlane')]
            .find(el => el.getAttribute('bpmnElement')?.split(':').at(-1) === activePlane);
          const missing = [...(plane?.children ?? [])]
            .filter(el => ['BPMNShape', 'BPMNEdge'].includes(el.localName))
            .map(el => el.getAttribute('bpmnElement')?.split(':').at(-1)).filter(id => id && !registry.get(id));
          const warnings = imported.warnings.map(warning => warning.message);
          return { mode: warnings.length || missing.length ? 'document' : 'modeler',
            reason: warnings.length || missing.length ? 'stock_modeler_partial_import' : 'stock_modeler_complete_import',
            warnings, missing_visuals: missing };
        } catch (failure) {
          return { mode: 'document', reason: 'stock_modeler_import_error', error: String(failure) };
        } finally { modeler.destroy(); }
      }, await readFile(item.input, 'utf8'));
      item.mode = item.capability.mode;
    }
  } finally { await probe.close(); }
  for (const item of selected.filter(item => item.mode === 'modeler')) {
    const directory = path.join(output, item.id);
    await mkdir(directory, { recursive: true });
    const xml = await readFile(item.input, 'utf8');
    const result = { id: item.id, origin: item.origin, capability: item.capability, input: relative(item.input),
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
        const nodes = elements.filter(el => !el.labelTarget && !el.waypoints
          && /(?:Task|Activity|Event|Gateway|SubProcess|Transaction)$/.test(el.type));
        const overlaps = [];
        for (let i = 0; i < nodes.length; i++) for (const right of nodes.slice(i + 1)) {
          const left = nodes[i];
          if (left.host === right || right.host === left || left.businessObject.$parent !== right.businessObject.$parent) continue;
          const width = Math.min(left.x + left.width, right.x + right.width) - Math.max(left.x, right.x);
          const height = Math.min(left.y + left.height, right.y + right.height) - Math.max(left.y, right.y);
          if (width > 3 && height > 3) overlaps.push({ left: left.id, right: right.id, width, height });
        }
        const lanes = elements.filter(el => el.type === 'bpmn:Lane').flatMap(lane =>
          (lane.businessObject.flowNodeRef ?? []).map(ref => {
            const node = window.modeler.get('elementRegistry').get(ref.id);
            return { lane: lane.id, node: ref.id, visible: Boolean(node), contained: !node || contains(lane, node) };
          }));
        const unlabelledConditions = elements.filter(el => el.type === 'bpmn:SequenceFlow'
          && el.businessObject.conditionExpression && !el.businessObject.name)
          .map(el => ({ id: el.id, condition: el.businessObject.conditionExpression.body }));
        const boundaryAttachments = elements.filter(el => el.type === 'bpmn:BoundaryEvent')
          .map(el => ({ id: el.id, expected_host: el.businessObject.attachedToRef?.id, actual_host: el.host?.id,
            attached: el.host?.id === el.businessObject.attachedToRef?.id }));
        const taskIntersections = [];
        for (const edge of elements.filter(el => el.waypoints)) for (const task of nodes.filter(el => /Task$|CallActivity$/.test(el.type))) {
          for (let index = 1; index < edge.waypoints.length; index++) {
            const a = edge.waypoints[index - 1], b = edge.waypoints[index];
            const horizontal = a.y === b.y && a.y > task.y + 2 && a.y < task.y + task.height - 2
              && Math.min(Math.max(a.x, b.x), task.x + task.width - 2) - Math.max(Math.min(a.x, b.x), task.x + 2) > 3;
            const vertical = a.x === b.x && a.x > task.x + 2 && a.x < task.x + task.width - 2
              && Math.min(Math.max(a.y, b.y), task.y + task.height - 2) - Math.max(Math.min(a.y, b.y), task.y + 2) > 3;
            if (horizontal || vertical) taskIntersections.push({ edge: edge.id, task: task.id, segment: index,
              is_endpoint: task === edge.source || task === edge.target });
          }
        }
        const technicalRows = nodes.filter(el => /Event$|Gateway$/.test(el.type) && el.type !== 'bpmn:BoundaryEvent')
          .flatMap(node => {
            const scopeLanes = elements.filter(el => el.type === 'bpmn:Lane'
              && el.businessObject.$parent?.$parent === node.businessObject.$parent);
            const assigned = scopeLanes.some(lane => (lane.businessObject.flowNodeRef ?? []).some(ref => ref.id === node.id));
            const bottom = Math.max(...scopeLanes.map(lane => lane.y + lane.height));
            return scopeLanes.length && !assigned && node.y >= bottom - 1
              ? [{ id: node.id, type: node.type, scope: node.businessObject.$parent.id, y: node.y, lanes_bottom: bottom }] : [];
          });
        return { warnings: imported.warnings.map(w => ({ message: w.message, element: w.element?.id })),
          rendered_ids: elements.map(el => el.id), fit_zoom: canvas.zoom(), nested,
          node_overlaps: overlaps, lane_containment: lanes, unlabelled_conditions: unlabelledConditions,
          boundary_attachments: boundaryAttachments,
          paths_through_task_interiors: taskIntersections,
          unassigned_flow_nodes_below_lanes: technicalRows,
          boundary_paths_through_host: boundaryPathsThroughHost,
          subprocesses: elements.filter(el => !el.labelTarget
            && ['bpmn:SubProcess', 'bpmn:Transaction', 'bpmn:AdHocSubProcess'].includes(el.type))
            .map(el => ({ id: el.id, x: el.x, y: el.y, width: el.width, height: el.height })),
          svg: (await window.modeler.saveSVG()).svg };
      }, xml);
      await page.screenshot({ path: path.join(directory, 'import-fit.png') });
      await writeFile(path.join(directory, 'import.svg'), result.import.svg, 'utf8');
      await renderSavedSvg(result.import.svg, directory);
      delete result.import.svg;
      await page.evaluate(() => { window.modeler.get('canvas').zoom(1); });
      await page.screenshot({ path: path.join(directory, '100percent.png') });
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
          function opaqueContent(el) {
            if (el.nodeType !== Node.ELEMENT_NODE) return { type: el.nodeType, text: el.nodeValue };
            return { tag: `{${el.namespaceURI}}${el.localName}`,
              attrs: [...el.attributes].filter(a => a.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
                .map(a => [`${a.namespaceURI ?? ''}:${a.localName}`, a.value]).sort((a, b) => a[0].localeCompare(b[0])),
              children: [...el.childNodes].map(opaqueContent) };
          }
          function visit(el, key, parent = null, parentStrict = false) {
            if (el.namespaceURI !== namespace) return;
            const strict = parentStrict || ['extensionElements', 'documentation'].includes(el.localName);
            const attrs = Object.fromEntries([...el.attributes].filter(a => a.namespaceURI !== 'http://www.w3.org/2000/xmlns/')
              .map(a => [`${a.namespaceURI ?? ''}:${a.localName}`, a.value]));
            if (el.getAttribute('id') === editedId) delete attrs[':name'];
            const directText = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE)
              .map(n => n.textContent).join('').trim();
            const typeName = el.getAttributeNS('http://www.w3.org/2001/XMLSchema-instance', 'type')?.split(':').at(-1)
              ?? `t${el.localName[0].toUpperCase()}${el.localName.slice(1)}`;
            items.set(key, { tag: el.localName, parent, attrs, text: directText, strict,
              opaque: strict ? opaqueContent(el) : null, defaults: strict ? {} : defaults(typeName) });
            const counts = {};
            for (const child of el.children) {
              if (child.namespaceURI !== namespace) continue;
              const index = counts[child.localName] = (counts[child.localName] ?? 0) + 1;
              // Lane memberships are sets; bpmn-js moves the edited member to their end.
              const suffix = child.localName === 'flowNodeRef' ? `=${child.textContent.trim()}` : `[${index}]`;
              visit(child, child.id ? `#${child.id}` : `${key}/${child.localName}${suffix}`, key, strict);
            }
          }
          visit(parsed.documentElement, '#definitions');
          return items;
        }
        const left = snapshot(before), right = snapshot(after), changes = [], normalizedDefaults = [], normalizedBoundaryLanes = [];
        function normativeCompensationDefault(item, name, first, second) {
          const isTrue = value => value === undefined || value === 'true' || value === '1';
          return !item.strict && item.tag === 'compensateEventDefinition' && name === ':waitForCompletion'
            && first !== second && isTrue(first) && isTrue(second);
        }
        function recordNormativeDefault(key, name, first, second) {
          normalizedDefaults.push({ key, attribute: name, before: first ?? null, after: second ?? null,
            normative_default: 'true', default_source: 'OMG BPMN 2.0.2, Table 10.94, p.263',
            default_source_url: 'https://www.omg.org/spec/BPMN/2.0.2/PDF#page=293' });
        }
        for (const [key, item] of left) {
          const next = right.get(key);
          if (!next) { changes.push({ key, kind: 'missing' }); continue; }
          if (item.tag !== next.tag || item.text !== next.text) changes.push({ key, kind: 'tag_or_text', before: item, after: next });
          if (item.parent !== next.parent) changes.push({ key, kind: 'semantic_parent', before: item.parent, after: next.parent });
          if (JSON.stringify(item.opaque) !== JSON.stringify(next.opaque)) changes.push({ key, kind: 'metadata_content' });
          for (const [name, value] of Object.entries(item.attrs)) {
            if (next.attrs[name] !== value) {
              if (normativeCompensationDefault(item, name, value, next.attrs[name])) {
                recordNormativeDefault(key, name, value, next.attrs[name]);
              } else if (next.attrs[name] === undefined && item.defaults[name] === value) {
                normalizedDefaults.push({ key, attribute: name, explicit_value: value, schema_default: item.defaults[name] });
              } else changes.push({ key, kind: 'attribute', name, before: value, after: next.attrs[name] });
            }
          }
          for (const [name, value] of Object.entries(next.attrs)) {
            if (!(name in item.attrs)) {
              if (normativeCompensationDefault(item, name, undefined, value)) recordNormativeDefault(key, name, undefined, value);
              else if (item.defaults[name] !== value) changes.push({ key, kind: 'added_attribute', name, value });
            }
          }
        }
        for (const [key, item] of right) {
          if (left.has(key)) continue;
          const boundary = item.tag === 'flowNodeRef' && left.get(`#${item.text}`);
          const host = boundary?.attrs[':attachedToRef'];
          const laneKey = key.split('/flowNodeRef=')[0];
          if (boundary?.tag === 'boundaryEvent' && host && left.has(`${laneKey}/flowNodeRef=${host}`)) {
            normalizedBoundaryLanes.push({ boundary: item.text, host, lane: laneKey.slice(1), scope: boundary.parent,
              reason: 'bpmn-js records attached boundary in the lane already containing its host after the host moves' });
          } else changes.push({ key, kind: 'added_element', after: item });
        }
        return { preserved: changes.length === 0, changes, normalized_defaults: normalizedDefaults,
          normalized_boundary_lane_memberships: normalizedBoundaryLanes,
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
  const documents = selected.filter(item => item.mode === 'document');
  if (documents.length) {
    const credentials = { username: `visual_${Date.now()}`, password: randomBytes(32).toString('base64url') };
    const provision = spawnSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m',
      'backend.app.manage_users', 'create', credentials.username, '--password-stdin'], {
      cwd: root, input: `${credentials.password}\n`, encoding: 'utf8', windowsHide: true,
    });
    if (provision.status !== 0) throw new Error('Local document-editor test account provisioning failed.');
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true });
    const page = await context.newPage();
    await authenticateSmokePage(page, report.ui_base_url, credentials, expect);
    for (const item of documents) {
      const directory = path.join(output, item.id);
      await mkdir(directory, { recursive: true });
      const xml = await readFile(item.input, 'utf8');
      const result = { id: item.id, origin: item.origin, capability: item.capability, renderer: 'production_full_document',
        input: relative(item.input), input_sha256: hash(xml), status: 'FAIL', page_errors: [], console_errors: [] };
      const onError = error => result.page_errors.push(String(error));
      const onConsole = message => { if (message.type() === 'error') result.console_errors.push(message.text()); };
      page.on('pageerror', onError); page.on('console', onConsole);
      report.cases.push(result);
      try {
        await page.goto(report.ui_base_url);
        result.ui_assets = await page.evaluate(() => [...document.scripts].map(script => script.src).filter(Boolean));
        await page.getByTestId('import-file').setInputFiles({ name: `${item.id}.bpmn`, mimeType: 'application/xml', buffer: Buffer.from(xml) });
        await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
        await expect(page.getByTestId('document-di')).toBeVisible();
        await expect(page.getByTestId('export')).toBeEnabled();
        const desiredDiagram = await page.evaluate(value => {
          const doc = new DOMParser().parseFromString(value, 'application/xml');
          const shapes = [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')];
          const activity = [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', '*')]
            .find(el => /Task$|Activity$|Conversation$|^conversation$|^dataObject$/.test(el.localName)
              && shapes.some(shape => shape.getAttribute('bpmnElement')?.split(':').at(-1) === el.getAttribute('id')));
          const shape = [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')]
            .find(el => el.getAttribute('bpmnElement')?.split(':').at(-1) === activity?.getAttribute('id'));
          const rootId = shape?.parentElement?.getAttribute('bpmnElement')?.split(':').at(-1);
          return [...doc.querySelectorAll('[id]')].find(el => el.getAttribute('id') === rootId)?.getAttribute('name');
        }, xml);
        const diagramSelect = page.getByTestId('document-diagram-select');
        if (desiredDiagram && await diagramSelect.count()) {
          await diagramSelect.click();
          await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: desiredDiagram }).click();
          await page.keyboard.press('Escape');
        }
        const toggle = page.getByTestId('inspector-toggle');
        if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click();
        await page.mouse.move(20, 20);
        await page.screenshot({ path: path.join(directory, 'import-ui.png'), animations: 'disabled' });
        result.initial = await page.evaluate(value => {
          const document = new DOMParser().parseFromString(value, 'application/xml');
          const visible = [...document.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', '*')]
            .filter(el => /Task$|Activity$|Conversation$|^conversation$|^dataObject$|^subChoreography$/.test(el.localName)
              && window.document.querySelector(`[data-testid="document-shape-${el.getAttribute('id')}"]`));
          const activity = visible.find(el => el.localName === 'dataObject') ?? visible[0];
          if (!activity) throw new Error('No visible named semantic element to edit in document view');
          const svg = window.document.querySelector('[data-testid="document-di"]');
          const computed = getComputedStyle(svg);
          const clone = svg.cloneNode(true);
          clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
          clone.setAttribute('style', 'background:white;font-family:Arial,sans-serif;font-size:12px');
          for (const text of clone.querySelectorAll('text')) text.setAttribute('style', 'font-family:Arial,sans-serif;font-size:12px;fill:#28364b');
          const expectedDi = [...document.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', '*')]
            .filter(el => ['BPMNShape', 'BPMNEdge'].includes(el.localName));
          const missingDi = expectedDi.filter(el => {
            const kind = el.localName === 'BPMNShape' ? 'shape' : 'edge';
            const id = el.getAttribute('bpmnElement')?.split(':').at(-1);
            const planeRoot = el.parentElement?.getAttribute('bpmnElement')?.split(':').at(-1);
            const active = [...document.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')]
              .find(shape => shape.getAttribute('bpmnElement')?.split(':').at(-1) === activity.getAttribute('id'))
              ?.parentElement?.getAttribute('bpmnElement')?.split(':').at(-1);
            return planeRoot === active && !window.document.querySelector(`[data-testid="document-${kind}-${id}"]`);
          }).map(el => el.getAttribute('bpmnElement'));
          return { id: activity.getAttribute('id'), name: activity.getAttribute('name') || '',
            missing_di: missingDi,
            elements: document.getElementsByTagName('*').length,
            shapes: [...window.document.querySelectorAll('[data-testid^="document-shape-"]')].map(el => el.getAttribute('data-testid')),
            markers: [...window.document.querySelectorAll('[data-bpmn-marker]')].map(el => el.getAttribute('data-bpmn-marker')),
            viewBox: svg.getAttribute('viewBox'), renderedWidth: svg.getBoundingClientRect().width,
            actualScale: svg.getBoundingClientRect().width / svg.viewBox.baseVal.width,
            css: { color: computed.color, font: computed.font }, svg: new XMLSerializer().serializeToString(clone) };
        }, xml);
        await writeFile(path.join(directory, 'import.svg'), result.initial.svg, 'utf8');
        await renderSavedSvg(result.initial.svg, directory);
        delete result.initial.svg;
        assert.deepEqual(result.initial.missing_di, [], 'Document renderer omitted supplied DI');
        await page.getByTestId('document-di').screenshot({ path: path.join(directory, 'import-diagram.png'), animations: 'disabled' });
        if (process.env.BPMN_EXPECT_TRUE_SCALE === '1') {
          const dimensions = () => page.getByTestId('document-di').evaluate(svg => {
            const box = svg.getBoundingClientRect(), parent = svg.parentElement.getBoundingClientRect();
            return { scale: box.width / svg.viewBox.baseVal.width,
              visible: box.left >= parent.left && box.top >= parent.top && box.right <= parent.right + 1 && box.bottom <= parent.bottom + 1 };
          });
          await page.getByTestId('document-di-fit').click();
          await expect.poll(async () => (await dimensions()).visible).toBe(true);
          result.viewport = { fit: await dimensions() };
          await page.getByTestId('document-di-scale').click();
          await expect.poll(async () => Math.abs((await dimensions()).scale - 1)).toBeLessThan(.001);
          result.viewport.native = await dimensions();
          await page.mouse.move(20, 20);
          await page.screenshot({ path: path.join(directory, 'document-100percent.png'), animations: 'disabled' });
          await page.getByTestId('document-di-fit').click();
          await expect.poll(async () => (await dimensions()).visible).toBe(true);
          await page.setViewportSize({ width: 1280, height: 800 });
          await expect.poll(async () => (await dimensions()).visible).toBe(true);
          result.viewport.resized_fit = await dimensions();
          await page.screenshot({ path: path.join(directory, 'document-fit-resized.png'), animations: 'disabled' });
          await page.setViewportSize({ width: 1600, height: 1000 });
          await expect.poll(async () => (await dimensions()).visible).toBe(true);
        }
        await page.getByTestId(`document-shape-${result.initial.id}`).click();
        const name = `${result.initial.name} — проверено`;
        await page.getByTestId('document-attr-name').fill(name);
        await page.getByTestId('document-apply').click();
        await page.getByRole('button', { name: 'Положение и размер', exact: true }).click();
        const y = page.getByTestId('document-geometry-0-y');
        const beforeY = Number(await y.inputValue());
        await y.fill(String(beforeY + 12)); await page.getByTestId('document-apply').click();
        await page.getByTestId('validate').click();
        await expect(page.getByTestId('check-xsd')).toContainText('Пройдено');
        await expect(page.getByTestId('check-graph')).toContainText('Пройдено');
        await expect(page.getByTestId('check-layout')).toContainText('Пройдено');
        await expect(page.getByTestId('export')).toBeEnabled();
        const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
        await (await pending).saveAs(path.join(directory, 'edited.bpmn'));
        const edited = await readFile(path.join(directory, 'edited.bpmn'), 'utf8');
        result.semantic_preservation = await page.evaluate(({ original, edited, id }) => {
          const parse = text => new DOMParser().parseFromString(text, 'application/xml');
          function canonical(el) {
            if (el.nodeType === Node.TEXT_NODE || el.nodeType === Node.CDATA_SECTION_NODE) return el.textContent.trim() || null;
            if (el.nodeType === Node.COMMENT_NODE) return { comment: el.textContent };
            if (el.nodeType !== Node.ELEMENT_NODE || el.namespaceURI === 'http://www.omg.org/spec/BPMN/20100524/DI') return null;
            const attrs = [...el.attributes].filter(a => a.namespaceURI !== 'http://www.w3.org/2000/xmlns/'
              && !(el.getAttribute('id') === id && a.localName === 'name'))
              .map(a => [`{${a.namespaceURI || ''}}${a.localName}`, a.value]).sort((a, b) => a[0].localeCompare(b[0]));
            return { tag: `{${el.namespaceURI}}${el.localName}`, attrs, children: [...el.childNodes].map(canonical).filter(x => x !== null) };
          }
          const left = parse(original), right = parse(edited);
          const bounds = doc => [...doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape')]
            .filter(el => el.getAttribute('bpmnElement')?.split(':').at(-1) === id)
            .map(el => { const b = el.getElementsByTagNameNS('http://www.omg.org/spec/DD/20100524/DC', 'Bounds')[0];
              return { id: el.getAttribute('id'), x: Number(b.getAttribute('x')), y: Number(b.getAttribute('y')) }; });
          return { preserved: JSON.stringify(canonical(left.documentElement)) === JSON.stringify(canonical(right.documentElement)),
            original_elements: left.getElementsByTagName('*').length, exported_elements: right.getElementsByTagName('*').length,
            name: [...right.querySelectorAll('[id]')].find(el => el.getAttribute('id') === id)?.getAttribute('name'),
            original_bounds: bounds(left), exported_bounds: bounds(right) };
        }, { original: xml, edited, id: result.initial.id });
        assert.equal(result.semantic_preservation.preserved, true, 'Document semantics changed beyond explicit rename');
        assert.equal(result.semantic_preservation.name, name);
        assert.equal(result.semantic_preservation.exported_bounds[0].y, beforeY + 12);
        await page.getByTestId('import-file').setInputFiles({ name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(edited) });
        await expect(page.getByTestId(`document-shape-${result.initial.id}`)).toContainText(name);
        await expect(page.getByTestId('export')).toBeEnabled();
        if (await toggle.getAttribute('aria-expanded') === 'true') await toggle.click();
        await page.screenshot({ path: path.join(directory, 'edited-ui.png'), animations: 'disabled' });
        result.edited_sha256 = hash(edited); result.xsd = 'passed'; result.graph = 'passed'; result.layout = 'passed';
        result.reimport = 'passed'; result.edit = { id: result.initial.id, name, dy: 12 };
        assert.deepEqual(result.page_errors, []);
        result.status = 'PASS';
      } catch (error) { result.error = String(error).replaceAll(credentials.password, '[REDACTED]'); }
      finally { page.off('pageerror', onError); page.off('console', onConsole); }
      result.input_unchanged = hash(await readFile(item.input)) === result.input_sha256;
      if (!result.input_unchanged) { result.status = 'FAIL'; result.error = 'Source XML changed during check'; }
      await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
      console.log(JSON.stringify({ id: item.id, status: result.status, error: result.error }));
    }
    await context.close();
  }
} finally { await browser.close(); }
report.total = report.cases.length;
report.passed = report.cases.filter(item => item.status === 'PASS').length;
report.status = report.total && report.passed === report.total ? 'PASS' : 'FAIL';
report.geometry_findings = report.cases.flatMap(item => (item.import?.boundary_paths_through_host ?? [])
  .map(finding => ({ case: item.id, origin: item.origin, ...finding })));
report.geometry_status = report.geometry_findings.length ? 'findings' : 'passed';
report.visual_findings = report.cases.flatMap(item => [
  ...(item.import?.boundary_paths_through_host ?? []).map(finding => ({ kind: 'boundary_path_through_host', ...finding })),
  ...(item.import?.paths_through_task_interiors ?? []).map(finding => ({ kind: 'path_through_task', ...finding })),
  ...(item.import?.node_overlaps ?? []).map(finding => ({ kind: 'node_overlap', ...finding })),
  ...(item.import?.unassigned_flow_nodes_below_lanes ?? []).map(finding => ({ kind: 'technical_row_below_lanes', ...finding })),
  ...(item.import?.unlabelled_conditions ?? []).map(finding => ({ kind: 'condition_not_visible', ...finding })),
].map(finding => ({ case: item.id, origin: item.origin, ...finding })));
await writeFile(path.join(output, 'geometry-findings.json'), JSON.stringify(report.visual_findings, null, 2), 'utf8');
await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
await writeFile(path.join(output, `report-${report.executed_at.replace(/[:.]/g, '-')}.json`), JSON.stringify(report, null, 2), 'utf8');
console.log(JSON.stringify({ status: report.status, passed: report.passed, total: report.total }));
process.exitCode = report.status === 'PASS' ? 0 : 1;
