import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { csrfHeaders, loginTestAccount } from './auth-helpers';

const directObject = `<b:definitions xmlns:b="http://www.omg.org/spec/BPMN/20100524/MODEL"
  xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
  xmlns:di="http://www.omg.org/spec/DD/20100524/DI" id="Definitions" targetNamespace="urn:direct-data-test">
  <b:process id="P" name="Проверка данных"><b:startEvent id="Start"/>
    <b:userTask id="Task" name="Подготовить заключение"><b:ioSpecification id="IO">
      <b:dataOutput id="Out"/><b:inputSet id="Inputs"/>
      <b:outputSet id="Outputs"><b:dataOutputRefs>Out</b:dataOutputRefs></b:outputSet>
    </b:ioSpecification><b:dataOutputAssociation id="Write"><b:sourceRef>Out</b:sourceRef>
      <b:targetRef>Document</b:targetRef></b:dataOutputAssociation></b:userTask>
    <b:endEvent id="End"/><b:dataObject id="Document" name="Заключение"/>
    <b:dataObjectReference id="Reference" name="Заключение" dataObjectRef="Document"/>
    <b:sequenceFlow id="F1" sourceRef="Start" targetRef="Task"/>
    <b:sequenceFlow id="F2" sourceRef="Task" targetRef="End"/>
  </b:process><bpmndi:BPMNDiagram id="Diagram"><bpmndi:BPMNPlane id="Plane" bpmnElement="P">
    <bpmndi:BPMNShape id="ShStart" bpmnElement="Start"><dc:Bounds x="40" y="120" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShTask" bpmnElement="Task"><dc:Bounds x="140" y="98" width="160" height="80"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShEnd" bpmnElement="End"><dc:Bounds x="360" y="120" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShDocument" bpmnElement="Document"><dc:Bounds x="190" y="250" width="50" height="65"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShReference" bpmnElement="Reference"><dc:Bounds x="350" y="250" width="50" height="65"/></bpmndi:BPMNShape>
    <bpmndi:BPMNEdge id="EdgeF1" bpmnElement="F1"><di:waypoint x="76" y="138"/><di:waypoint x="140" y="138"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeF2" bpmnElement="F2"><di:waypoint x="300" y="138"/><di:waypoint x="360" y="138"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeWrite" bpmnElement="Write" sourceElement="ShTask" targetElement="ShDocument">
      <di:waypoint x="220" y="178"/><di:waypoint x="220" y="220"/><di:waypoint x="215" y="220"/><di:waypoint x="215" y="250"/>
    </bpmndi:BPMNEdge></bpmndi:BPMNPlane></bpmndi:BPMNDiagram></b:definitions>`;

test('Выбор редактора учитывает прямую фигуру DataObject отдельно для каждой диаграммы', async ({ page }) => {
  await page.goto('/');
  const result = await page.evaluate(async xml => {
    const mod = await import('/src/xmlDocument.ts');
    const doc = mod.parseBpmn(xml);
    const original = mod.documentDiagrams(doc)[0].canvas;
    const diagram = mod.elementById(doc, 'Diagram')!;
    const clone = diagram.cloneNode(true) as Element;
    [clone, ...Array.from(clone.querySelectorAll('[id]'))].forEach(el => el.setAttribute('id', `${el.getAttribute('id')}_2`));
    clone.querySelector('[bpmnElement="Document"]')!.remove();
    doc.documentElement.appendChild(clone);
    const independently = mod.documentDiagrams(doc).map(item => item.canvas);
    mod.elementById(doc, 'ShDocument')!.remove();
    return { original, independently, hiddenDefinition: mod.documentDiagrams(doc)[0].canvas };
  }, directObject);
  expect(result).toEqual({ original: false, independently: [false, true], hiddenDefinition: true });
});

test('Прямой DataObject: документ, связь, переименование, перемещение и экспорт без подмены ID', async ({ page }) => {
  await loginTestAccount(page); await page.goto('/');
  const validation = await page.request.post('/api/validate', { data: { xml: directObject }, headers: await csrfHeaders(page) });
  const checked = await validation.json();
  expect(checked.checks, JSON.stringify(checked.issues)).toMatchObject({ xsd: 'passed', graph: 'passed', layout: 'passed' });
  await page.getByTestId('import-file').setInputFiles({ name: 'direct-data.bpmn', mimeType: 'application/xml', buffer: Buffer.from(directObject) });
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  await expect(page.getByTestId('document-shape-Document')).toBeVisible();
  await expect(page.getByTestId('document-shape-Reference')).toBeVisible();
  await expect(page.getByTestId('document-edge-Write')).toBeVisible();
  await page.getByTestId('document-shape-Document').click();
  await page.getByTestId('document-attr-name').fill('Проверенное заключение'); await page.getByTestId('document-apply').click();
  await page.getByRole('button', { name: 'Положение и размер', exact: true }).click();
  await page.getByTestId('document-geometry-0-x').fill('215'); await page.getByTestId('document-apply').click();
  await page.getByTestId('validate').click(); await expect(page.getByTestId('export')).toBeEnabled();
  const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
  const exported = readFileSync((await (await pending).path())!, 'utf8');
  const result = await page.evaluate(({ original, current }) => {
    const parse = (xml: string) => new DOMParser().parseFromString(xml, 'application/xml');
    const before = parse(original), after = parse(current);
    function semantic(doc: XMLDocument) {
      return Array.from(doc.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', '*')).map(el => ({
        kind: el.localName, id: el.getAttribute('id'),
        attrs: Array.from(el.attributes).filter(attr => attr.namespaceURI !== 'http://www.w3.org/2000/xmlns/'
          && !(el.getAttribute('id') === 'Document' && attr.localName === 'name')).map(attr => [attr.name, attr.value]).sort(),
        text: Array.from(el.childNodes).filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent?.trim()).join(''),
      }));
    }
    return { same: JSON.stringify(semantic(before)) === JSON.stringify(semantic(after)),
      name: after.querySelector('[id="Document"]')?.getAttribute('name'),
      target: after.querySelector('[id="Write"]')?.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', 'targetRef')[0].textContent,
      x: after.querySelector('[id="ShDocument"]')?.firstElementChild?.getAttribute('x') };
  }, { original: directObject, current: exported });
  expect(result).toEqual({ same: true, name: 'Проверенное заключение', target: 'Document', x: '215' });
  await page.getByTestId('import-file').setInputFiles({ name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(exported) });
  await expect(page.getByTestId('document-shape-Document')).toContainText('Проверенное заключение');
  await expect(page.getByTestId('document-edge-Write')).toBeVisible();
});
