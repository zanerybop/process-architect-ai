import { expect, test } from '@playwright/test';
import { compensationDefaultCases } from './compensation-default-fixtures';

test('Нормативный default компенсации не скрывает false, расширения и metadata', async ({ page }) => {
  await page.goto('/');
  const results = await page.evaluate(async cases => {
    const mod = await import('/src/xmlDocument.ts');
    return cases.map(item => ({ name: item.name, loss: mod.roundtripLoss(item.before, item.after).length > 0 }));
  }, compensationDefaultCases);
  expect(results).toEqual(compensationDefaultCases.map(({ name, loss }) => ({ name, loss })));
});
