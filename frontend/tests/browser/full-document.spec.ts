import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { csrfHeaders, loginTestAccount } from './auth-helpers';
import { advancedProcess, fullDocument } from './document-fixtures';

const evidence = resolve('../artifacts/full-bpmn-ui');
test.beforeEach(async ({ page }) => { mkdirSync(evidence, { recursive: true }); await loginTestAccount(page); await page.goto('/'); });
async function openFull(page: Page, xml = fullDocument) {
  const response = await page.request.post('/api/validate', { data: { xml }, headers: await csrfHeaders(page) });
  expect(response.status()).toBe(200);
  const check = await response.json();
  expect(check.checks.xsd, JSON.stringify(check.issues)).toBe('passed');
  expect(check.checks.graph, JSON.stringify(check.issues)).toBe('passed');
  await page.getByTestId('import-file').setInputFiles({ name: 'full-document.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
}
async function pickSelect(page: Page, testId: string, label: string) {
  await page.getByTestId(testId).click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: label }).click();
}
async function downloadXml(page: Page) {
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('export')).toBeEnabled();
  const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
  const download = await pending; const path = await download.path();
  expect(path).not.toBeNull(); return readFileSync(path!, 'utf8');
}

test('Хореография: bands, имена, геометрия, скрытый XML, undo и текущий экспорт', async ({ page }) => {
  await openFull(page);
  await expect(page.getByTestId('document-shape-Ch_order')).toBeVisible();
  await expect(page.getByTestId('document-shape-Buyer')).toContainText('Покупатель');
  await expect(page.getByTestId('document-shape-Seller')).toContainText('Поставщик');
  await page.getByTestId('document-shape-Ch_order').click();
  await page.getByTestId('document-attr-name').fill('Согласовать & передать заказ');
  await page.getByTestId('document-apply').click();
  await expect(page.getByTestId('document-shape-Ch_order')).toContainText('Согласовать & передать заказ');
  await expect(page.getByTestId('export')).toBeDisabled();
  await page.getByRole('button', { name: 'Отменить изменение документа', exact: true }).click();
  await expect(page.getByTestId('document-shape-Ch_order')).toContainText('Передать заказ');
  await page.getByRole('button', { name: 'Повторить изменение документа', exact: true }).click();
  await page.getByRole('button', { name: 'Положение и размер', exact: true }).click();
  await page.getByTestId('document-geometry-0-x').fill('230');
  await page.getByTestId('document-apply').click();
  const exported = await downloadXml(page);
  const result = await page.evaluate((xml) => {
    const doc = new DOMParser().parseFromString(xml, 'application/xml');
    const select = (id: string) => Array.from(doc.querySelectorAll('[id]')).find((item) => item.getAttribute('id') === id)!;
    return { name: select('Ch_order').getAttribute('name'), rootCount: doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNDiagram').length,
      bounds: ['Sh_order', 'Sh_buyer', 'Sh_seller'].map((id) => select(id).firstElementChild!.getAttribute('x')),
      mixed: new XMLSerializer().serializeToString(doc.getElementsByTagNameNS('urn:example:private', 'note')[0]) };
  }, exported);
  expect(result.name).toBe('Согласовать & передать заказ');
  expect(result.rootCount).toBe(2); expect(result.bounds).toEqual(['230', '230', '230']);
  expect(result.mixed).toContain('до<v:part value="1"/>после');
  expect(result.mixed).toContain('empty=""'); expect(exported).toContain('Сохранить комментарий документа');
  await page.getByTestId('inspector-toggle').click();
  await page.screenshot({ path: resolve(evidence, 'choreography-edited.png'), animations: 'disabled' });
  await page.getByTestId('import-file').setInputFiles({ name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(exported) });
  await expect(page.getByTestId('document-shape-Ch_order')).toContainText('Согласовать & передать заказ');
});

test('Conversation: несколько диаграмм, hexagon, links и schema child CRUD', async ({ page }) => {
  await openFull(page);
  await pickSelect(page, 'document-diagram-select', 'Разговоры');
  await expect(page.getByTestId('document-shape-Talk').locator('polygon')).toBeVisible();
  await expect(page.getByTestId('document-edge-Talk_left')).toBeVisible();
  await page.getByTestId('document-shape-Talk').click();
  await page.getByTestId('document-attr-name').fill('Новые условия заказа'); await page.getByTestId('document-apply').click();
  await pickSelect(page, 'document-child-type', 'bpmn:documentation');
  await page.getByTestId('document-add-child').click();
  const tree = page.locator('.document-tree-panel');
  await tree.getByPlaceholder('Найти элемент').fill('documentation');
  await tree.getByRole('button', { name: /^documentation_[a-f0-9]+$/ }).click();
  await page.getByTestId('document-text-new').fill('Условия согласованы аналитиком'); await page.getByTestId('document-apply').click();
  const exported = await downloadXml(page);
  expect(exported).toContain('Условия согласованы аналитиком'); expect(exported).toContain('Новые условия заказа');
  expect(exported).toContain('choreographyTask'); expect(exported).toContain('conversationLink');
  await page.getByTestId('inspector-toggle').click();
  await page.screenshot({ path: resolve(evidence, 'conversation-edited.png'), animations: 'disabled' });
  await page.getByTestId('document-delete').click(); await page.getByRole('button', { name: 'Удалить', exact: true }).click();
  expect(await downloadXml(page)).not.toContain('Условия согласованы аналитиком');
});

test('Canvas loss detection сохраняет mixed content и не объявляет partial import полным', async ({ page }) => {
  const original = readFileSync(resolve('../artifacts/m1/P01/generated.bpmn'), 'utf8');
  const xml = original.replace('targetNamespace=', 'xmlns:v="urn:example:private" targetNamespace=')
    .replace(/(<bpmn:task[^>]*>)/, '$1<bpmn:extensionElements><v:note>до<v:part/>после</v:note></bpmn:extensionElements>');
  expect(xml).not.toBe(original);
  await openFull(page, xml);
  await expect(page.getByText(/Графический редактор изменяет данные документа/)).toBeVisible();
  expect(await downloadXml(page)).toContain('до<v:part/>после');
});

test('Loss/QName helpers: hidden data, foreign refs, xsi:type, DTD и независимые DI', async ({ page }) => {
  const result = await page.evaluate(async (fixture) => {
    const mod = await import('/src/xmlDocument.ts');
    const xml = `<b:definitions xmlns:b="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:v="urn:foreign" xmlns:t="urn:local" targetNamespace="urn:local"><b:process id="P"><b:task id="A" name="Before"><b:extensionElements><v:meta>before<v:part/>after</v:meta></b:extensionElements></b:task><b:task id="B"/><b:sequenceFlow id="F" sourceRef="t:A" targetRef="v:A"/></b:process></b:definitions>`;
    const renamed = xml.replace('name="Before"', 'name="After"');
    const lost = renamed.replace('<v:meta>before<v:part/>after</v:meta>', '<v:meta>beforeafter<v:part/></v:meta>');
    const whitespace = xml.replace('<v:meta>before<v:part/>after</v:meta>', '<v:meta xml:space="preserve"> <v:part/> </v:meta>');
    const document = mod.parseBpmn(xml); mod.setXmlAttribute(document, mod.elementById(document, 'A'), 'id', 'Renamed');
    const flow = mod.elementById(document, 'F');
    const element = document.createElementNS(mod.XMLNS.bpmn, 'b:flowElement'); element.setAttributeNS(mod.XMLNS.xsi, 'xsi:type', 'b:tUserTask');
    let dtd = false; try { mod.parseBpmn('<!DOCTYPE definitions>' + xml); } catch { dtd = true; }
    const moved = mod.parseBpmn(fixture);
    const diagram = mod.elementById(moved, 'Diagram_choreography');
    const clone = diagram.cloneNode(true);
    [clone, ...Array.from(clone.querySelectorAll('[id]'))].forEach((item: Element) => item.setAttribute('id', item.getAttribute('id') + '_second'));
    moved.documentElement.appendChild(clone);
    mod.moveShape(moved, mod.elementById(moved, 'Sh_order'), 25, 0);
    return { safe: mod.roundtripLoss(xml, renamed, new Set(['A'])), loss: mod.roundtripLoss(xml, lost, new Set(['A'])),
      whitespaceLoss: mod.roundtripLoss(whitespace, whitespace.replace('> <v:part/> <', '><v:part/><'), new Set(['A'])),
      local: flow.getAttribute('sourceRef'), foreign: flow.getAttribute('targetRef'), effective: mod.semanticName(element), dtd,
      movement: ['Sh_order', 'Sh_buyer', 'Sh_order_second', 'Sh_buyer_second'].map((id) => mod.boundsOf(mod.elementById(moved, id)).x),
      edges: ['Edge_first', 'Edge_first_second'].map((id) => mod.elementById(moved, id).lastElementChild.getAttribute('x')) };
  }, fullDocument);
  expect(result.safe).toEqual([]); expect(result.loss.length).toBeGreaterThan(0);
  expect(result.whitespaceLoss.length).toBeGreaterThan(0);
  expect(result.local).toBe('t:Renamed'); expect(result.foreign).toBe('v:A'); expect(result.effective).toBe('userTask'); expect(result.dtd).toBe(true);
  expect(result.movement).toEqual([215, 215, 190, 190]); expect(result.edges).toEqual(['215', '190']);
});

test('DI geometry: attached boundary, container contents и конкретные choreography bands', async ({ page }) => {
  const result = await page.evaluate(async ({ process, choreography }) => {
    const mod = await import('/src/xmlDocument.ts');
    const doc = mod.parseBpmn(process);
    mod.moveShape(doc, mod.elementById(doc, 'ShReview'), 30, 20);
    mod.moveShape(doc, mod.elementById(doc, 'ShTransaction'), 15, 0);
    const bounds = (id: string) => mod.boundsOf(mod.elementById(doc, id));
    const nested = ['ShTransaction', 'ShInnerStart', 'ShService', 'ShInnerEnd'].map((id) => bounds(id).x);
    const choreographyDoc = mod.parseBpmn(choreography);
    const plane = mod.elementById(choreographyDoc, 'Plane_choreography');
    const bpmn = mod.XMLNS.bpmn, di = mod.XMLNS.bpmndi;
    const sourceActivity = mod.elementById(choreographyDoc, 'Ch_order');
    const activity = sourceActivity.cloneNode(true); activity.setAttribute('id', 'SecondActivity'); sourceActivity.parentElement.appendChild(activity);
    for (const id of ['Sh_order', 'Sh_buyer', 'Sh_seller']) {
      const shape = mod.elementById(choreographyDoc, id).cloneNode(true); shape.setAttribute('id', id + '_repeat');
      if (id === 'Sh_order') shape.setAttribute('bpmnElement', 'SecondActivity'); else shape.setAttribute('choreographyActivityShape', 'Sh_order_repeat');
      shape.firstElementChild.setAttribute('x', '600'); plane.appendChild(shape);
    }
    for (const [id, source, target, x] of [['E1', 'Sh_buyer', 'Sh_seller', 370], ['E2', 'Sh_buyer_repeat', 'Sh_seller_repeat', 780]]) {
      const edge = choreographyDoc.createElementNS(di, 'bpmndi:BPMNEdge');
      edge.setAttribute('id', String(id)); edge.setAttribute('bpmnElement', 'Offer'); edge.setAttribute('sourceElement', String(source)); edge.setAttribute('targetElement', String(target));
      for (const y of [122, 217]) { const point = choreographyDoc.createElementNS(mod.XMLNS.di, 'di:waypoint'); point.setAttribute('x', String(x)); point.setAttribute('y', String(y)); edge.appendChild(point); }
      plane.appendChild(edge);
    }
    mod.moveShape(choreographyDoc, mod.elementById(choreographyDoc, 'Sh_order'), 20, 0);
    const explicit = ['E1', 'E2'].map((id) => mod.elementById(choreographyDoc, id).firstElementChild.getAttribute('x'));
    for (const id of ['E1', 'E2']) { const edge = mod.elementById(choreographyDoc, id); edge.removeAttribute('sourceElement'); edge.removeAttribute('targetElement'); }
    mod.moveShape(choreographyDoc, mod.elementById(choreographyDoc, 'Sh_order'), 10, 0);
    const inferred = ['E1', 'E2'].map((id) => mod.elementById(choreographyDoc, id).firstElementChild.getAttribute('x'));
    return { host: bounds('ShReview'), boundary: bounds('ShTimer'), nested, explicit, inferred,
      boundaryEdge: mod.elementById(doc, 'EdgeFT').firstElementChild.getAttribute('x') };
  }, { process: advancedProcess, choreography: fullDocument });
  expect(result.host).toMatchObject({ x: 155, y: 100 }); expect(result.boundary).toMatchObject({ x: 210, y: 162 });
  expect(result.boundaryEdge).toBe('228'); expect(result.nested).toEqual([330, 350, 399, 542]);
  expect(result.explicit).toEqual(['390', '780']); expect(result.inferred).toEqual(['400', '780']);
});

test('Свёрнутый subprocess не выдаёт скрытые дочерние фигуры за отсутствующий DI', async ({ page }) => {
  const collapsed = advancedProcess.replace('isExpanded="true"', 'isExpanded="false"')
    .replace(/<bpmndi:BPMNShape id="Sh(?:InnerStart|Service|InnerEnd)"[\s\S]*?<\/bpmndi:BPMNShape>/g, '')
    .replace(/<bpmndi:BPMNEdge id="EdgeI[12]"[\s\S]*?<\/bpmndi:BPMNEdge>/g, '');
  await page.getByTestId('import-file').setInputFiles({ name: 'collapsed.bpmn', mimeType: 'application/xml', buffer: Buffer.from(collapsed) });
  await expect(page.getByTestId('export')).toBeEnabled();
  if (await page.getByTestId('modeler').isVisible()) await pickSelect(page, 'document-view-select', 'Документ BPMN');
  await expect(page.getByTestId('document-unplaced')).toHaveCount(0);
  await expect(page.getByTestId('document-shape-Transaction').locator('[data-bpmn-marker="collapsed"]')).toBeVisible();
});

test('Потеря расширения после замены типа открывает последнюю полную версию', async ({ page }) => {
  const original = readFileSync(resolve('../artifacts/m1/P01/generated.bpmn'), 'utf8');
  const xml = original.replace('targetNamespace=', 'xmlns:v="urn:example:private" targetNamespace=')
    .replace('<bpmn:task id="p01_check"', '<bpmn:task v:secret="keep" id="p01_check"');
  expect(xml).toContain('v:secret="keep"');
  await page.getByTestId('import-file').setInputFiles({ name: 'opaque.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
  await expect(page.getByTestId('export')).toBeEnabled();
  await expect(page.getByTestId('modeler')).toBeVisible();
  await page.locator('.djs-element[data-element-id="p01_check"]').click();
  await page.locator('.djs-context-pad [data-action="replace"]').click();
  await page.locator('.djs-popup [data-id="replace-with-service-task"]').click();
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  await expect(page.getByTestId('error')).toContainText('Последнее изменение затронуло скрытые данные');
  const exported = await downloadXml(page);
  expect(exported).toContain('v:secret="keep"'); expect(exported).not.toContain('serviceTask');
});

test('Документ без DI: редактирование, экспорт и явное построение диаграмм', async ({ page }) => {
  const noDi = fullDocument.replace(/<bpmndi:BPMNDiagram\b[\s\S]*?<\/bpmndi:BPMNDiagram>/g, '');
  await openFull(page, noDi);
  await expect(page.getByTestId('document-unplaced')).toBeVisible();
  await expect(page.getByTestId('document-di')).toHaveCount(0);
  expect(await downloadXml(page)).not.toContain('BPMNDiagram');
  await page.getByTestId('inspector-toggle').click();
  const laidOut = page.waitForResponse((response) => response.url().endsWith('/api/bpmn/layout'));
  await page.getByTestId('document-layout').click();
  expect((await laidOut).status()).toBe(200);
  await expect(page.getByTestId('document-shape-Ch_order')).toBeVisible();
  const current = await downloadXml(page);
  expect(current).toContain('BPMNDiagram'); expect(current).toContain('до<v:part value="1"/>после');
});

test('Полная DI-нотация: timer boundary, transaction, MI, loop, data и внешние QName', async ({ page }) => {
  const validation = await page.request.post('/api/validate', { data: { xml: advancedProcess }, headers: await csrfHeaders(page) });
  const checked = await validation.json();
  expect(checked.checks, JSON.stringify(checked.issues)).toMatchObject({ xsd: 'passed', graph: 'passed', layout: 'passed' });
  await page.getByTestId('import-file').setInputFiles({ name: 'advanced.bpmn', mimeType: 'application/xml', buffer: Buffer.from(advancedProcess) });
  await expect(page.getByTestId('export')).toBeEnabled();
  if (await page.getByTestId('modeler').isVisible()) await pickSelect(page, 'document-view-select', 'Документ BPMN');
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  const shape = (id: string) => page.getByTestId(`document-shape-${id}`);
  await expect(shape('Timer').locator('[data-bpmn-marker="timer"]')).toBeVisible();
  await expect(shape('Timer').locator('ellipse[stroke-dasharray]')).toHaveCount(2);
  await expect(shape('Start').locator('[data-bpmn-marker="message"]')).toBeVisible();
  await expect(shape('End').locator('[data-bpmn-marker="terminate"]')).toBeVisible();
  await expect(shape('Review').locator('[data-bpmn-marker="sequential"]')).toBeVisible();
  await expect(shape('Call').locator('[data-bpmn-marker="loop"]')).toBeVisible();
  await expect(shape('Transaction').locator('[data-bpmn-symbol="transaction"] > rect')).toHaveCount(2);
  await expect(shape('DataRef').locator('[data-bpmn-marker="collection"]')).toBeVisible();
  await shape('Call').click();
  await expect(page.getByTestId('document-attr-calledElement')).toHaveValue('ext:Remote');
  await page.getByTestId('document-attr-name').fill('Вызвать внешний процесс'); await page.getByTestId('document-apply').click();
  expect(await downloadXml(page)).toContain('calledElement="ext:Remote"');
  await page.getByTestId('inspector-toggle').click();
  await page.mouse.move(20, 20);
  await page.screenshot({ path: resolve(evidence, 'advanced-notation.png'), animations: 'disabled' });
});
