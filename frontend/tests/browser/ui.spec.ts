import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { csrfHeaders, loginTestAccount, selectExample } from './auth-helpers';

// Real UI/API/XSD/storage with an explicitly injected trusted fixture runner.
// These tests never count as live LLM or container-isolation evidence.
const artifactDirectory = resolve('../artifacts/m3');
const cases = JSON.parse(readFileSync(resolve('../examples/cases.json'), 'utf8')).cases;

async function setInspector(page: Page, open: boolean) {
  const toggle = page.getByTestId('inspector-toggle');
  if ((await toggle.getAttribute('aria-expanded')) !== String(open)) await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', String(open));
}

async function expectPoolsInsideCanvas(page: Page, poolIds: string[]) {
  expect(poolIds.length).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate((ids) => {
    const palette = document.querySelector('.djs-palette')!.getBoundingClientRect();
    const canvas = document.querySelector('[data-testid="modeler"]')!.getBoundingClientRect();
    return Math.min(...ids.flatMap((id) => {
      const pool = document.querySelector(`.djs-element[data-element-id="${id}"] .djs-visual > rect`)!.getBoundingClientRect();
      return [pool.left - palette.right, canvas.right - pool.right];
    }));
  }, poolIds), { message: 'Рамки пулов должны помещаться справа от палитры и внутри холста' }).toBeGreaterThanOrEqual(8);
}

test.beforeEach(async ({ page }, testInfo) => {
  testInfo.annotations.push({ type: 'test_transport', description: 'trusted_fixture_unit; real bpmn-js UI' });
  mkdirSync(artifactDirectory, { recursive: true });
  await loginTestAccount(page);
  await page.goto('/');
  await expect(page.getByTestId('mode')).toContainText(/mock/i);
  await expect(page.getByTestId('example-select')).toBeEnabled();
  await expect(page.getByTestId('inspector-toggle')).toHaveAttribute('aria-expanded', 'false');
});

async function generate(page: Page, caseId: string) {
  await selectExample(page, caseId);
  await expect(page.getByTestId('description')).toHaveValue(cases.find((item: any) => item.id === caseId).text);
  const responsePromise = page.waitForResponse(response =>
    response.url().endsWith('/api/generate') && response.request().method() === 'POST');
  await page.getByTestId('generate').click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  return response.json();
}

async function ready(page: Page, caseId: string) {
  const result = await generate(page, caseId);
  expect(result.status).toBe('ready');
  expect(result.mode).toBe('mock');
  await expect(page.locator(`.djs-element[data-element-id="${result.ir.nodes[0].id}"]`)).toBeVisible();
  await setInspector(page, true);
  await expect(page.getByTestId('check-import')).toContainText('Пройдено');
  await expect(page.getByTestId('export')).toBeEnabled();
  await setInspector(page, false);
  await page.getByRole('button', { name: 'Вписать', exact: true }).click();
  return result;
}

async function geometry(page: Page, xml: string, nodeId: string) {
  return page.evaluate(({ input, id }) => {
    const document = new DOMParser().parseFromString(input, 'application/xml');
    const node = Array.from(document.querySelectorAll('[id]')).find(item => item.id === id);
    const shape = Array.from(document.getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/DI', 'BPMNShape'))
      .find(item => item.getAttribute('bpmnElement') === id);
    const bounds = shape?.getElementsByTagNameNS('http://www.omg.org/spec/DD/20100524/DC', 'Bounds')[0];
    return { name: node?.getAttribute('name'), x: Number(bounds?.getAttribute('x')), y: Number(bounds?.getAttribute('y')) };
  }, { input: xml, id: nodeId });
}

async function rename(page: Page, nodeId: string, name: string) {
  await page.locator(`.djs-element[data-element-id="${nodeId}"]`).click();
  await page.getByTestId('element-name').fill(name);
  await page.getByTestId('rename').click();
  await expect(page.getByTestId('export')).toBeDisabled();
  await setInspector(page, true);
  await expect(page.getByTestId('audit-stale')).toBeVisible();
  await setInspector(page, false);
  await page.getByRole('button', { name: 'Вписать', exact: true }).click();
}

for (const scenario of [
  { id: 'P05', node: 'p05_create', width: 1440, height: 900 },
  { id: 'P10', node: 'p10_send_request', width: 1280, height: 800 },
]) {
  test(`${scenario.id}: UI правка, перенос, текущий экспорт и повторный импорт (${scenario.width})`, async ({ page }) => {
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await page.screenshot({ path: resolve(artifactDirectory, `redesign-empty-${scenario.width}x${scenario.height}.png`) });
    const original = await ready(page, scenario.id);
    for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 800 }]) {
      await page.setViewportSize(viewport);
      await page.getByRole('button', { name: 'Вписать', exact: true }).click();
      await page.screenshot({ path: resolve(artifactDirectory, `redesign-${scenario.id}-${viewport.width}x${viewport.height}.png`) });
    }
    await page.setViewportSize({ width: scenario.width, height: scenario.height });
    await page.getByRole('button', { name: 'Вписать', exact: true }).click();
    await page.screenshot({ path: resolve(artifactDirectory, `${scenario.id}-generated-${scenario.width}x${scenario.height}.png`) });
    const before = await geometry(page, original.bpmn_xml, scenario.node);
    const name = 'Проверено аналитиком: План & факт <2026>';
    await rename(page, scenario.node, name);
    const rectangle = page.locator(`.djs-element[data-element-id="${scenario.node}"] .djs-visual > rect`).first();
    const box = await rectangle.boundingBox();
    expect(box).not.toBeNull();
    const x = box!.x + box!.width / 2;
    const y = box!.y + box!.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 7, y, { steps: 3 });
    await page.mouse.move(x + 42, y, { steps: 12 });
    await page.mouse.up();
    await expect(page.getByTestId('export')).toBeDisabled();
    await page.getByTestId('validate').click();
    await expect(page.getByTestId('check-xsd')).toContainText('Пройдено');
    await expect(page.getByTestId('export')).toBeEnabled();
    // Opening the inspector must fit the actual resized canvas automatically.
    // A manual "fit" here would conceal stale cached canvas dimensions.
    const poolIds = await page.evaluate((xml) => Array.from(
      new DOMParser().parseFromString(xml, 'application/xml')
        .getElementsByTagNameNS('http://www.omg.org/spec/BPMN/20100524/MODEL', 'participant'),
    ).map(pool => pool.getAttribute('id')!), original.bpmn_xml);
    await expectPoolsInsideCanvas(page, poolIds);
    await page.screenshot({ path: resolve(artifactDirectory, `redesign-${scenario.id}-inspector-${scenario.width}x${scenario.height}.png`) });
    const revisionPromise = page.waitForResponse(response =>
      response.url().includes(`/api/generations/${original.id}/revisions`) && response.request().method() === 'PUT');
    const downloadPromise = page.waitForEvent('download');
    await page.getByTestId('export').click();
    const download = await downloadPromise;
    const revision = await revisionPromise;
    expect(revision.status()).toBe(200);
    const path = resolve(artifactDirectory, `${scenario.id}-ui-edited.bpmn`);
    await download.saveAs(path);
    const xml = readFileSync(path, 'utf8');
    const after = await geometry(page, xml, scenario.node);
    expect(after.name).toBe(name);
    expect(after.x).not.toBe(before.x);
    // A horizontal drag may snap to the grid, but must not jump a toolbar's height.
    expect(Math.abs(after.y - before.y)).toBeLessThanOrEqual(20);
    const checks = await page.request.post('/api/validate', { data: { xml }, headers: await csrfHeaders(page) });
    const validation = await checks.json();
    expect(validation.checks).toMatchObject({ xsd: 'passed', graph: 'passed', layout: 'passed', import: 'not_run' });
    const stored = await (await page.request.get(`/api/generations/${original.id}`)).json();
    expect(stored.bpmn_xml).toBe(xml);
    expect(stored.revision_id).not.toBe(original.revision_id);
    expect(stored.audit_stale).toBe(true);
    await page.getByTestId('import-file').setInputFiles({
      name: 'edited.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml),
    });
    await expect(page.getByTestId('check-import')).toContainText('Пройдено');
    await page.locator(`.djs-element[data-element-id="${scenario.node}"]`).click();
    await expect(page.getByTestId('element-name')).toHaveValue(name);
    const translated = await page.locator(`.djs-element[data-element-id="${scenario.node}"]`).evaluate(element => {
      const matrix = (element as SVGGraphicsElement).transform.baseVal.consolidate()?.matrix;
      return { x: matrix?.e, y: matrix?.f };
    });
    expect(translated.x).toBeCloseTo(after.x, 3);
    expect(translated.y).toBeCloseTo(after.y, 3);
    await page.screenshot({ path: resolve(artifactDirectory, `${scenario.id}-${scenario.width}x${scenario.height}.png`) });
    writeFileSync(resolve(artifactDirectory, `${scenario.id}-ui-evidence.json`), JSON.stringify({
      test_transport: 'trusted_fixture_unit', live_generation_verified: false, worker_isolation_verified: false,
      viewport: { width: scenario.width, height: scenario.height }, before, after, validation,
      generation_id: original.id, revision_id: stored.revision_id,
    }, null, 2));
  });
}

test('Уточнения об условиях и компенсации, затем неизвестный mock видимы в UI', async ({ page }) => {
  expect((await generate(page, 'P07')).status).toBe('needs_clarification');
  await expect(page.getByTestId('status')).toContainText(/уточн/i);
  await expect(page.getByTestId('export')).toBeDisabled();
  expect((await generate(page, 'P14')).status).toBe('needs_clarification');
  await expect(page.getByTestId('status')).toContainText(/уточн/i);
  await page.getByTestId('description').fill('Новый процесс, которого нет в fixture-корпусе.');
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('status')).toContainText(/ошиб/i);
  await expect(page.getByTestId('description')).toHaveValue('Новый процесс, которого нет в fixture-корпусе.');
  await expect(page.getByTestId('export')).toBeDisabled();
});

test('Явный отказ по ресурсным ограничениям виден без подмены схемой', async ({ page }) => {
  const prior = await generate(page, 'P07');
  await page.route('**/api/generate', (route) => route.fulfill({ json: { ...prior, id: 'resource-limit-fixture', status: 'unsupported',
    questions: [], issues: [{ code: 'RESOURCE_LIMIT', category: 'technical', severity: 'error', element_ids: [], message: 'Описание превышает ресурсы одного построения.' }] } }));
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('status')).toContainText(/поддерж/i);
  await expect(page.getByTestId('last-attempt')).toContainText('Описание превышает ресурсы одного построения.');
  await expect(page.getByTestId('export')).toBeDisabled();
});

test('Сетевая ошибка сохраняет описание и последнюю успешную схему', async ({ page }) => {
  await ready(page, 'P01');
  const task = page.locator('.djs-element[data-element-id="p01_check"]');
  await expect(task).toBeVisible();
  await page.getByTestId('description').fill('Новое описание для проверки сбоя сети');
  await page.route('**/api/generate', route => route.abort('failed'));
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('status')).toContainText(/ошиб/i);
  await expect(page.getByTestId('description')).toHaveValue('Новое описание для проверки сбоя сети');
  await expect(task).toBeVisible();
});

test('Новая генерация сохраняет ручную правку предыдущей версии в истории', async ({ page }) => {
  const original = await ready(page, 'P01');
  const name = 'Ручная проверка до новой генерации';
  await rename(page, 'p01_check', name);
  await ready(page, 'P10');
  const history = page.getByTestId('history-select');
  await history.click();
  expect(await page.locator('.ant-select-dropdown:visible .ant-select-item-option').count()).toBeGreaterThanOrEqual(2);
  const stored = await (await page.request.get(`/api/generations/${original.id}`)).json();
  expect((await geometry(page, stored.bpmn_xml, 'p01_check')).name).toBe(name);
  await page.getByTestId(`history-option-${original.id}`).click();
  await page.locator('.djs-element[data-element-id="p01_check"]').click();
  await expect(page.getByTestId('element-name')).toHaveValue(name);
});

test('Повреждённый импорт и пустой ввод не создают готовую схему', async ({ page }) => {
  await expect(page.getByTestId('export')).toBeDisabled();
  await page.getByTestId('description').fill('');
  await expect(page.getByTestId('generate')).toBeDisabled();
  await page.getByTestId('import-file').setInputFiles({
    name: 'broken.bpmn', mimeType: 'application/xml', buffer: Buffer.from('<broken'),
  });
  await expect(page.getByTestId('status')).toContainText(/ошиб/i);
  await expect(page.getByTestId('export')).toBeDisabled();
});

test('Удаление связи разрывает процесс и блокирует экспорт после проверки', async ({ page }) => {
  await ready(page, 'P01');
  // bpmn-js reconnects neighbours when a task is deleted; delete the flow itself.
  const midpoint = await page.locator('.djs-element[data-element-id="p01_f1"] .djs-visual > path').evaluate(element => {
    const path = element as SVGPathElement;
    const point = path.getPointAtLength(path.getTotalLength() / 2);
    const screen = new DOMPoint(point.x, point.y).matrixTransform(path.getScreenCTM()!);
    return { x: screen.x, y: screen.y };
  });
  await page.mouse.click(midpoint.x, midpoint.y);
  await page.locator('.djs-context-pad [data-action="delete"]').click();
  await expect(page.getByTestId('export')).toBeDisabled();
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('check-graph')).toContainText('Ошибка');
  await expect(page.getByTestId('export')).toBeDisabled();
});

test('Лимит описания сохраняет весь ввод и отключает построение', async ({ page }) => {
  await page.getByTestId('description').fill('');
  await expect(page.getByTestId('generate')).toBeDisabled();
  const tooLong = 'Я'.repeat(20_001);
  await page.getByTestId('description').fill(tooLong);
  await expect(page.getByTestId('description')).toHaveValue(tooLong);
  await expect(page.getByTestId('generate')).toBeDisabled();
  await expect(page.getByText('Превышен лимит 20 000 символов.')).toBeVisible();
});

test('LIVE без ключа показывает настройку подключения и не подменяется MOCK', async ({ page }) => {
  await page.route('**/api/health', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      status: 'ok', mode: 'live', generation_available: false,
      dependencies: { worker: true, xsd_files_available: true, llm_configured: false },
    }),
  }));
  await page.reload();
  await expect(page.getByTestId('mode')).toContainText('LIVE');
  await page.getByTestId('description').fill('Оператор регистрирует обращение и завершает процесс.');
  await expect(page.getByTestId('generate')).toBeDisabled();
  await page.getByRole('button', { name: 'Генерация недоступна' }).click();
  await expect(page.getByText('Для LIVE не настроено подключение к модели.')).toBeVisible();
});
