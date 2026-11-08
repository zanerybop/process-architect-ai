import { expect, test } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { loginTestAccount } from '../browser/auth-helpers';

test('Live XML: choreography import, manual edit, current export and reimport', async ({ page }) => {
  test.info().annotations.push({ type: 'source', description: 'Existing real LLM/isolated worker artifact; browser API uses trusted fixture validation, no new model call.' });
  expect(process.env.BPMN_LIVE_XML, 'Set BPMN_LIVE_XML to the generated.bpmn being verified').toBeTruthy();
  const xmlPath = resolve(process.env.BPMN_LIVE_XML!);
  const original = readFileSync(xmlPath, 'utf8');
  const evidence = resolve('../artifacts/full-bpmn-ui'); mkdirSync(evidence, { recursive: true });
  await loginTestAccount(page); await page.goto('/');
  await page.getByTestId('import-file').setInputFiles({ name: 'live-choreography.bpmn', mimeType: 'application/xml', buffer: Buffer.from(original) });
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  await expect(page.getByTestId('export')).toBeEnabled();
  const id = await page.evaluate((xml) => new DOMParser().parseFromString(xml, 'application/xml')
    .getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', 'choreographyTask')[0].getAttribute('id')!, original);
  await page.getByTestId(`document-shape-${id}`).click();
  const previousName = await page.getByTestId('document-attr-name').inputValue();
  const editedName = `${previousName} — проверено в редакторе`;
  await page.getByTestId('document-attr-name').fill(editedName); await page.getByTestId('document-apply').click();
  await expect(page.getByTestId('export')).toBeDisabled();
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('check-xsd')).toContainText('Пройдено');
  await expect(page.getByTestId('check-graph')).toContainText('Пройдено');
  await expect(page.getByTestId('check-layout')).toContainText('Пройдено');
  await expect(page.getByTestId('check-import')).toContainText('Не проверено');
  const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
  const download = await pending; const exportedPath = resolve(evidence, 'live-choreography-edited.bpmn'); await download.saveAs(exportedPath);
  const exported = readFileSync(exportedPath, 'utf8'); expect(exported).toContain(editedName);
  const content = await page.evaluate(({ original, exported }) => {
    const parse = (xml: string) => new DOMParser().parseFromString(xml, 'application/xml');
    const before = parse(original), after = parse(exported);
    const byLocal = (doc: Document, name: string) => Array.from(doc.getElementsByTagNameNS('*', name));
    const canonical = (doc: Document) => byLocal(doc, 'BPMNDiagram').map((element) => new XMLSerializer().serializeToString(element));
    return { preservedDI: JSON.stringify(canonical(before)) === JSON.stringify(canonical(after)),
      beforeCount: before.getElementsByTagName('*').length, afterCount: after.getElementsByTagName('*').length,
      bands: byLocal(after, 'BPMNShape').filter((shape) => shape.hasAttribute('participantBandKind')).length };
  }, { original, exported });
  expect(content.preservedDI).toBe(true); expect(content.afterCount).toBe(content.beforeCount); expect(content.bands).toBeGreaterThan(0);
  await page.getByTestId('inspector-toggle').click(); await page.mouse.move(20, 20);
  await page.screenshot({ path: resolve(evidence, 'live-choreography-edited.png'), animations: 'disabled' });
  await page.getByTestId('import-file').setInputFiles({ name: 'live-edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(exported) });
  await expect(page.getByTestId(`document-shape-${id}`)).toContainText(editedName);
  writeFileSync(resolve(evidence, 'live-choreography-ui.json'), JSON.stringify({
    source_xml: xmlPath, exported_xml: exportedPath, model_called_by_this_test: false,
    mode: 'document', edited_id: id, previous_name: previousName, edited_name: editedName, ...content,
    xsd: 'passed', graph: 'passed', layout: 'passed', bpmn_js_import: 'not_run', reimport: 'passed',
  }, null, 2));
});
