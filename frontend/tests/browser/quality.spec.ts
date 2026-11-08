import { expect, test } from '@playwright/test';
import { loginTestAccount, selectExample, csrfHeaders } from './auth-helpers';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const cases = JSON.parse(readFileSync(resolve('../examples/cases.json'), 'utf8')).cases;

test.beforeEach(async ({ page }, info) => {
  info.annotations.push({ type: 'test_transport', description: 'trusted_fixture_unit; real bpmn-js UI' });
  await loginTestAccount(page);
  await page.goto('/');
});

test('Чтение, поиск, источник, сравнение названия и доступные инструменты', async ({ page }) => {
  await selectExample(page, 'P01');
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('export')).toBeEnabled();
  await expect(page.getByTestId('modeler'), (await page.locator('.document-capability-note').allTextContents()).join(' ')).toBeVisible();
  const before = (await page.getByTestId('modeler').boundingBox())!.width;
  await page.getByTestId('reading-mode').click();
  await expect(page.getByTestId('description')).toBeHidden();
  await expect.poll(async () => (await page.getByTestId('modeler').boundingBox())!.width).toBeGreaterThan(before);
  await page.getByTestId('outline-select').click();
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option').filter({ hasText: 'Проверить заявку' }).click();
  await expect(page.getByTestId('element-name')).toHaveValue('Проверить заявку');
  await expect(page.getByRole('button', { name: /Масштаб 100%/ })).toBeVisible();
  // The owner expanded the product from the MVP profile to the full BPMN document.
  await expect(page.locator('.djs-palette [data-action="create.subprocess-expanded"]')).toBeVisible();
  await expect(page.locator('.djs-palette [data-action="create.intermediate-event"]')).toBeVisible();
  await page.getByTestId('element-name').fill('Проверить заявку аналитиком');
  await page.getByTestId('rename').click();
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('semantic-diff')).toContainText('Проверить заявку → Проверить заявку аналитиком');
  await expect(page.getByTestId('source-review')).toContainText('После правки проверьте связь заново.');
  await expect(page.getByTestId('source-coverage-row').first()).toBeVisible();
  await expect(page.getByTestId('check-behavior')).toContainText('Пройдено');
  await expect(page.getByTestId('export')).toBeEnabled();
});

test('Уточнения отправляют ответы вместе с неизменённым исходным описанием', async ({ page }) => {
  const readyResponse = await page.request.post('/api/generate', {
    data: { description: cases.find((item: any) => item.id === 'P01').text }, headers: await csrfHeaders(page),
  });
  const ready = await readyResponse.json();
  expect(ready.status).toBe('ready');
  await selectExample(page, 'P08');
  const source = await page.getByTestId('description').inputValue();
  await page.getByTestId('generate').click();
  const answers = page.locator('[data-testid^="clarification-answer-"]');
  await expect(answers.first()).toBeVisible();
  await expect(page.getByTestId('clarification-continue')).toBeDisabled();
  const count = await answers.count();
  for (let index = 0; index < count; index++) await answers.nth(index).fill(`Ответ аналитика ${index + 1}`);
  // Explicit response fixture checks UI state/data; API workflow is independently tested in Python.
  await page.route('**/api/generate', async route => {
    const sent = route.request().postDataJSON();
    expect(sent.description).toBe(source);
    expect(sent.clarification_answers).toHaveLength(count);
    expect(sent.clarification_answers[0].answer).toBe('Ответ аналитика 1');
    await route.fulfill({ json: ready });
  });
  await page.getByTestId('clarification-continue').click();
  await expect(page.getByTestId('export')).toBeEnabled();
  await expect(page.getByTestId('clarification-continue')).toHaveCount(0);
});
