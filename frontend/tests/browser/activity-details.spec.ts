import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { loginTestAccount } from './auth-helpers';
import { dataProcess, resourceProcess } from './activity-details-fixtures';

async function open(page: Page, xml: string) {
  await loginTestAccount(page); await page.goto('/');
  await page.getByTestId('import-file').setInputFiles({ name: 'activity.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
  await expect(page.getByTestId('export')).toBeEnabled();
}

async function mode(page: Page, label: string) {
  await page.getByTestId('document-view-select').click();
  await page.locator('.ant-select-dropdown:visible').getByText(label, { exact: true }).click();
  await page.keyboard.press('Escape');
}

async function selectDocument(page: Page, query: string) {
  await page.getByPlaceholder('Найти элемент', { exact: true }).fill(query);
  await page.locator('.document-tree-scroll button').first().click();
}

async function exportXml(page: Page) {
  if (!await page.getByTestId('export').isEnabled()) {
    await page.getByTestId('validate').click();
    await expect(page.getByTestId('export')).toBeEnabled();
  }
  const pending = page.waitForEvent('download'); await page.getByTestId('export').click();
  return readFileSync((await (await pending).path())!, 'utf8');
}

test('Сохранённый DB07: назначение ресурса видно в обоих редакторах и обновляется после правки', async ({ page }) => {
  await open(page, resourceProcess);
  const before = await exportXml(page);
  await page.locator('[data-element-id="register_change"] .djs-hit').click();
  await expect(page.getByTestId('activity-performers')).toHaveText('Исполнитель:Сервис');
  expect(await exportXml(page)).toBe(before);
  await mode(page, 'Документ BPMN');
  await selectDocument(page, 'Зарегистрировать изменение');
  await expect(page.getByTestId('activity-performers')).toHaveText('Исполнитель:Сервис');
  await selectDocument(page, 'Сервис');
  await page.getByTestId('document-attr-name').fill('Сервис изменений');
  await page.getByTestId('document-apply').click();
  await selectDocument(page, 'Зарегистрировать изменение');
  await expect(page.getByTestId('activity-performers')).toHaveText('Исполнитель:Сервис изменений');
  await mode(page, 'Графический редактор');
  await page.locator('[data-element-id="register_change"] .djs-hit').click();
  await expect(page.getByTestId('activity-performers')).toHaveText('Исполнитель:Сервис изменений');
  expect(await exportXml(page)).toContain('name="Сервис изменений"');
});

test('Сохранённый DB08: вход и выход следуют за текущими связями данных, без изменения XML от просмотра', async ({ page }) => {
  await open(page, dataProcess);
  const before = await exportXml(page);
  await page.locator('[data-element-id="check_contract"] .djs-hit').click();
  await expect(page.getByTestId('activity-candidates')).toHaveText('Возможный исполнитель:Юрист');
  await expect(page.getByTestId('activity-performers')).toHaveCount(0);
  await expect(page.getByTestId('activity-inputs')).toHaveText('Вход:Договор.pdf');
  await expect(page.getByTestId('activity-outputs')).toHaveText('Выход:Реестр договоров');
  expect(await exportXml(page)).toBe(before);
  await mode(page, 'Документ BPMN');
  await selectDocument(page, 'Проверить договор');
  await expect(page.getByTestId('activity-inputs')).toHaveText('Вход:Договор.pdf');
  await selectDocument(page, 'Договор.pdf');
  await page.getByTestId('document-attr-name').fill('Договор — новая редакция.pdf');
  await page.getByTestId('document-apply').click();
  await selectDocument(page, 'Проверить договор');
  await expect(page.getByTestId('activity-inputs')).toHaveText('Вход:Договор — новая редакция.pdf');
  await mode(page, 'Графический редактор');
  await page.locator('[data-element-id="check_contract"] .djs-hit').click();
  await expect(page.getByTestId('activity-inputs')).toHaveText('Вход:Договор — новая редакция.pdf');
  await expect(page.getByTestId('activity-outputs')).toHaveText('Выход:Реестр договоров');
});

test('Сводка не принимает чужие namespaces и metadata, наследует ближайшую lane и проверяет владельца IO', async ({ page }) => {
  await page.goto('/');
  const results = await page.evaluate(async () => {
    const modulePath = '/src/activityDetails.ts';
    const { activityDetails } = await import(modulePath);
    const ns = 'http://www.omg.org/spec/BPMN/20100524/MODEL';
    const render = (body: string, id = 'work') => {
      const doc = new DOMParser().parseFromString(`<b:definitions xmlns:b="${ns}" xmlns:v="urn:foreign" xmlns:t="urn:local" targetNamespace="urn:local">${body}</b:definitions>`, 'application/xml');
      return activityDetails(doc, Array.from(doc.getElementsByTagNameNS(ns, '*')).find(element => element.getAttribute('id') === id), null);
    };
    const resources = '<b:resource id="r" name="Сервис"/>';
    const task = '<b:serviceTask id="work"><b:performer><b:resourceRef>r</b:resourceRef></b:performer></b:serviceTask>';
    const lanes = '<b:laneSet><b:lane name="Старший"><b:flowNodeRef>sub</b:flowNodeRef></b:lane></b:laneSet>';
    const io = '<b:process><b:dataObject id="data" name="Документ"/><b:userTask id="work"><b:ioSpecification><b:dataInput id="input"/></b:ioSpecification><b:dataInputAssociation><b:sourceRef>data</b:sourceRef><b:targetRef>input</b:targetRef></b:dataInputAssociation></b:userTask><b:userTask><b:ioSpecification><b:dataInput id="other"/></b:ioSpecification></b:userTask></b:process>';
    return {
      direct: render(resources + '<b:process>' + task + '</b:process>'),
      external: render(resources + '<b:process>' + task.replace('>r<', '>v:r<') + '</b:process>'),
      localQName: render(resources + '<b:process>' + task.replace('>r<', '>t:r<') + '</b:process>'),
      fakeMetadata: render('<b:extensionElements>' + resources + '</b:extensionElements><b:process>' + task + '</b:process>'),
      foreignResource: render(resources.replaceAll('b:resource', 'v:resource') + '<b:process>' + task + '</b:process>'),
      inherited: render('<b:process>' + lanes + '<b:subProcess id="sub"><b:userTask id="work"/></b:subProcess></b:process>'),
      overridden: render('<b:process>' + lanes + '<b:subProcess id="sub"><b:laneSet><b:lane name="Младший"><b:flowNodeRef>work</b:flowNodeRef></b:lane></b:laneSet><b:userTask id="work"/></b:subProcess></b:process>'),
      input: render(io), wrongOwner: render(io.replace('<b:targetRef>input', '<b:targetRef>other')),
      reversed: render(io.replace('<b:sourceRef>data', '<b:sourceRef>input').replace('<b:targetRef>input', '<b:targetRef>data')),
      foreignData: render(io.replace('<b:dataObject id="data" name="Документ"/>', '<b:dataObject id="source"/><b:dataObjectReference id="data" name="Документ" dataObjectRef="v:source"/>')),
      wrongDataType: render(io.replace('<b:dataObject id="data" name="Документ"/>', '<b:dataStore id="source"/><b:dataObjectReference id="data" name="Документ" dataObjectRef="source"/>')),
    };
  });
  expect(results.direct.performers).toEqual(['Сервис']);
  expect(results.localQName.performers).toEqual(['Сервис']);
  for (const key of ['external', 'fakeMetadata', 'foreignResource'] as const) expect(results[key].performers).toEqual([]);
  expect(results.inherited.performers).toEqual(['Старший']);
  expect(results.overridden.performers).toEqual(['Младший']);
  expect(results.input.inputs).toEqual(['Документ']);
  expect(results.wrongOwner.inputs).toEqual([]); expect(results.reversed.inputs).toEqual([]);
  expect(results.foreignData.inputs).toEqual([]); expect(results.wrongDataType.inputs).toEqual([]);
});
