import { expect, test } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { loginTestAccount, selectExample } from './auth-helpers';

test('Fixture замечания: переход к элементу, точная цитата и устаревание после правки', async ({ page }, testInfo) => {
  testInfo.annotations.push({
    type: 'test_transport',
    description: 'trusted_fixture_unit API; one injected synthetic audit issue; real bpmn-js selection',
  });
  const sourceQuote = 'менеджер проверяет заявку';
  const injectedIssue = {
    code: 'TEST_AUDIT_QUOTE', category: 'model', severity: 'warning',
    message: 'Учебное замечание: сверьте проверку заявки с исходным описанием.',
    element_ids: ['p01_check'], source_quote: sourceQuote,
    suggested_action: 'Выберите элемент и прочитайте точную цитату.',
  };
  let generationId = '';
  await page.route('**/api/generate', async (route) => {
    const response = await route.fetch();
    const result = await response.json();
    expect(result.status).toBe('ready');
    expect(result.mode).toBe('mock');
    generationId = result.id;
    await route.fulfill({ response, json: { ...result, issues: [...result.issues, injectedIssue] } });
  });
  await loginTestAccount(page);
  await page.goto('/');
  await expect(page.getByTestId('mode')).toContainText('MOCK');
  await expect(page.getByTestId('request-mode')).toContainText('MOCK');
  await selectExample(page, 'P01');
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('check-import')).toContainText('Пройдено');
  await expect(page.getByText(injectedIssue.message, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'К элементу p01_check', exact: true }).click();
  const selected = page.locator('.djs-element[data-element-id="p01_check"]');
  await expect(selected).toHaveClass(/selected/);
  await expect(page.getByTestId('element-name')).toHaveValue('Проверить заявку');
  await expect(page.locator('.source-link blockquote')).toHaveText(sourceQuote);
  await expect(page.locator('.source-link')).toContainText('Источник элемента');
  await expect(page.getByTestId('audit-stale')).toHaveCount(0);

  await page.getByTestId('element-name').fill('Проверить заявку вручную');
  await page.getByTestId('rename').click();
  await expect(page.getByTestId('audit-stale')).toContainText('Аудит устарел');
  await expect(page.locator('.source-link')).toContainText('Исходная цитата; связь устарела');
  await expect(page.locator('.source-link blockquote')).toHaveText(sourceQuote);
  await expect(page.getByTestId('export')).toBeDisabled();

  // Service mode determines the next request; an already open mock generation
  // keeps its own provenance label even if the service is reconfigured for live.
  await page.route('**/api/health', (route) => route.fulfill({ json: {
    status: 'ok', mode: 'live', generation_available: false,
    dependencies: { worker: true, xsd_files_available: true, llm_configured: false },
  } }));
  await page.getByTestId('connection-refresh').click();
  await expect(page.getByTestId('request-mode')).toContainText('LIVE');
  await expect(page.getByTestId('mode')).toContainText('MOCK');
  await expect(page.getByTestId('generate')).toBeDisabled();

  const artifacts = resolve('../artifacts/m4');
  mkdirSync(artifacts, { recursive: true });
  await page.screenshot({ path: resolve(artifacts, 'audit-source-links.png') });
  writeFileSync(resolve(artifacts, 'audit-ui-evidence.json'), JSON.stringify({
    generation_id: generationId,
    generation_mode: 'mock',
    test_transport: 'trusted_fixture_unit',
    issue_origin: 'explicitly_injected_test_fixture',
    live_generation_verified: false,
    injected_issue: injectedIssue,
    verified: ['real_modeler_selection', 'exact_source_quote', 'stale_audit_after_edit',
      'current_mock_generation_distinct_from_next_live_request'],
  }, null, 2));
});
