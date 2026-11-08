import { expect, test, type Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { loginTestAccount } from './auth-helpers';
import { fullDocument } from './document-fixtures';

async function geometry(page: Page) {
  return page.getByTestId('document-di').evaluate((svg: SVGSVGElement) => {
    const viewport = svg.parentElement!;
    const box = svg.getBoundingClientRect(), parent = viewport.getBoundingClientRect();
    return { scale: box.width / svg.viewBox.baseVal.width,
      fitsWidth: viewport.scrollWidth <= viewport.clientWidth + 1,
      fitsHeight: viewport.scrollHeight <= viewport.clientHeight + 1,
      visible: box.left >= parent.left && box.top >= parent.top && box.right <= parent.right + 1 && box.bottom <= parent.bottom + 1,
      viewBox: svg.getAttribute('viewBox') };
  });
}

async function exportXml(page: Page) {
  const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
  const path = await (await pending).path();
  return readFileSync(path!, 'utf8');
}

test('Высокая conversation: fit по двум осям, настоящий 100%, resize без изменения XML', async ({ page }) => {
  const evidence = resolve('../artifacts/full-bpmn/visual-current/viewport-regression');
  mkdirSync(evidence, { recursive: true });
  const xml = readFileSync(resolve('../artifacts/full-bpmn/fixtures/F06_conversations/generated.bpmn'), 'utf8');
  await loginTestAccount(page); await page.goto('/');
  await page.getByTestId('import-file').setInputFiles({ name: 'conversations.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  await expect(page.getByTestId('export')).toBeEnabled();
  await page.getByTestId('document-diagram-select').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'Разговоры об отгрузке' }).click();
  const original = await exportXml(page);
  await expect.poll(async () => (await geometry(page)).visible).toBe(true);
  const fit = await geometry(page);
  expect(fit.fitsWidth).toBe(true); expect(fit.fitsHeight).toBe(true); expect(fit.scale).toBeLessThan(1);
  await expect(page.getByTestId('document-di-scale')).toHaveText(`${Math.round(fit.scale * 100)}%`);
  await page.screenshot({ path: resolve(evidence, 'conversation-fit.png'), animations: 'disabled' });

  await page.getByTestId('document-di-scale').click();
  await expect.poll(async () => Math.abs((await geometry(page)).scale - 1)).toBeLessThan(.001);
  await expect(page.getByTestId('document-di-scale')).toHaveText('100%');
  await page.screenshot({ path: resolve(evidence, 'conversation-100percent.png'), animations: 'disabled' });

  await page.getByTestId('document-di-fit').click();
  await expect.poll(async () => (await geometry(page)).visible).toBe(true);
  await page.setViewportSize({ width: 1200, height: 760 });
  await expect.poll(async () => (await geometry(page)).visible).toBe(true);
  const resized = await geometry(page);
  expect(resized.fitsWidth).toBe(true); expect(resized.fitsHeight).toBe(true);
  expect(resized.scale).toBeLessThan(fit.scale);
  expect(resized.viewBox).toBe(fit.viewBox);
  await page.screenshot({ path: resolve(evidence, 'conversation-fit-resized.png'), animations: 'disabled' });
  expect(await exportXml(page)).toBe(original);
});

test('Fit включает вынесенную за фигуры и waypoints подпись связи', async ({ page }) => {
  const xml = await page.evaluate(value => {
    const doc = new DOMParser().parseFromString(value, 'application/xml');
    const byId = (id: string) => Array.from(doc.querySelectorAll('[id]')).find(el => el.getAttribute('id') === id)!;
    byId('Ch_first').setAttribute('name', 'Вынесенная подпись связи');
    const label = doc.createElementNS('http://www.omg.org/spec/BPMN/20100524/DI', 'bpmndi:BPMNLabel');
    const bounds = doc.createElementNS('http://www.omg.org/spec/DD/20100524/DC', 'dc:Bounds');
    Object.entries({ x: '1600', y: '1200', width: '170', height: '40' }).forEach(([name, value]) => bounds.setAttribute(name, value));
    label.appendChild(bounds); byId('Edge_first').appendChild(label);
    return new XMLSerializer().serializeToString(doc);
  }, fullDocument);
  await loginTestAccount(page); await page.goto('/');
  await page.getByTestId('import-file').setInputFiles({ name: 'edge-label.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
  await expect(page.getByTestId('bpmn-document-editor')).toBeVisible();
  await expect.poll(async () => (await geometry(page)).visible).toBe(true);
  const fitted = await geometry(page);
  const [, , width, height] = fitted.viewBox!.split(' ').map(Number);
  expect(width).toBeGreaterThanOrEqual(1800); expect(height).toBeGreaterThanOrEqual(1270);
  const labelVisible = await page.getByTestId('document-edge-Ch_first').locator('text').evaluate(label => {
    const text = label.getBoundingClientRect(), viewport = label.closest('svg')!.parentElement!.getBoundingClientRect();
    return text.left >= viewport.left && text.right <= viewport.right && text.top >= viewport.top && text.bottom <= viewport.bottom;
  });
  expect(labelVisible).toBe(true);
});
