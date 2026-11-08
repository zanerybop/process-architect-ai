import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { loginTestAccount } from './auth-helpers';
import { fullDocument } from './document-fixtures';

for (const [loopType, marker] of [
  ['Standard', 'loop'], ['MultiInstanceSequential', 'sequential'], ['MultiInstanceParallel', 'parallel'], ['None', null],
] as const) {
  test(`Choreography loopType=${loopType}: верный маркер в теле и сохранение XML`, async ({ page }) => {
    await loginTestAccount(page); await page.goto('/');
    const xml = fullDocument.replace('id="Ch_order" name=', `id="Ch_order" loopType="${loopType}" name=`);
    await page.getByTestId('import-file').setInputFiles({ name: 'loop.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
    const task = page.getByTestId('document-shape-Ch_order');
    await expect(task).toBeVisible();
    if (marker) {
      const actual = task.locator(`[data-bpmn-marker="${marker}"]`);
      await expect(actual).toBeVisible();
      const glyph = await actual.boundingBox();
      const lower = await page.getByTestId('document-shape-Seller').boundingBox();
      expect(glyph!.y + glyph!.height).toBeLessThan(lower!.y);
    }
    await expect(task.locator('[data-bpmn-marker="loop"], [data-bpmn-marker="sequential"], [data-bpmn-marker="parallel"]')).toHaveCount(marker ? 1 : 0);
    await task.click(); await page.getByTestId('document-attr-name').fill('Передать проверенный заказ');
    await page.getByTestId('document-apply').click();
    await page.getByTestId('validate').click(); await expect(page.getByTestId('export')).toBeEnabled();
    const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
    const exported = readFileSync((await (await pending).path())!, 'utf8');
    const retained = await page.evaluate(text => {
      const doc = new DOMParser().parseFromString(text, 'application/xml');
      return doc.querySelector('[id="Ch_order"]')?.getAttribute('loopType');
    }, exported);
    expect(retained).toBe(loopType);
    await page.getByTestId('import-file').setInputFiles({ name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(exported) });
    await expect(page.getByTestId('document-shape-Ch_order')).toContainText('Передать проверенный заказ');
    if (marker) await expect(page.getByTestId('document-shape-Ch_order').locator(`[data-bpmn-marker="${marker}"]`)).toBeVisible();
  });
}
