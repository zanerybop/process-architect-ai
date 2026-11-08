import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { loginTestAccount } from './auth-helpers';
import { sensorRace } from './event-details-fixtures';

const browserErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => {
  const errors: string[] = []; browserErrors.set(page, errors);
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
});
test.afterEach(async ({ page }) => { expect(browserErrors.get(page), 'Browser errors').toEqual([]); });

async function open(page: Page, xml = sensorRace) {
  await loginTestAccount(page); await page.goto('/');
  await page.getByTestId('import-file').setInputFiles({ name: 'events.bpmn', mimeType: 'application/xml', buffer: Buffer.from(xml) });
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

test('События CH09: имя сообщения и таймер видны на холсте, просмотр не меняет XML', async ({ page }, info) => {
  await open(page);
  const before = await exportXml(page);
  await page.locator('[data-element-id="catch_msg"] .djs-hit').click();
  await expect(page.getByTestId('activity-messages')).toHaveText('Сообщение:Вода прозрачная');
  await page.screenshot({ path: info.outputPath('message-name.png'), animations: 'disabled' });
  await page.locator('[data-element-id="catch_timer"] .djs-hit').click();
  await expect(page.getByTestId('activity-timers')).toHaveText('Таймер:PT30M');
  await expect(page.getByTestId('activity-messages')).toHaveCount(0);
  expect(await exportXml(page)).toBe(before);
  await mode(page, 'Документ BPMN');
  await selectDocument(page, 'Вода прозрачная');
  await page.getByTestId('document-attr-name').fill('Датчик подтвердил прозрачность');
  await page.getByTestId('document-apply').click();
  await selectDocument(page, 'Сообщение датчика');
  await expect(page.getByTestId('activity-messages')).toHaveText('Сообщение:Датчик подтвердил прозрачность');
  await mode(page, 'Графический редактор');
  await page.locator('[data-element-id="catch_msg"] .djs-hit').click();
  await expect(page.getByTestId('activity-messages')).toHaveText('Сообщение:Датчик подтвердил прозрачность');
  const edited = await exportXml(page);
  expect(edited).toContain('name="Датчик подтвердил прозрачность"');
  expect(edited).toContain('messageRef="msg_water"');
});

test('Глобальная eventDefinitionRef читается через QName и обновляется после изменения текущего XML', async ({ page }, info) => {
  // Explicit UI fixture: use a reusable global definition and targetNamespace QName.
  const xml = sensorRace.replace('<bpmn:definitions ', '<bpmn:definitions xmlns:t="urn:process-architect:document" ')
    .replace('<bpmn:messageEventDefinition id="def_msg" messageRef="msg_water"/>', '<bpmn:eventDefinitionRef>t:def_msg</bpmn:eventDefinitionRef>')
    .replace('<bpmn:message id="msg_water"', '<bpmn:messageEventDefinition id="def_msg" messageRef="t:msg_water"/><bpmn:message id="msg_water"');
  await open(page, xml);
  await mode(page, 'Документ BPMN');
  await selectDocument(page, 'Сообщение датчика');
  await expect(page.getByTestId('activity-messages')).toHaveText('Сообщение:Вода прозрачная');
  const before = await exportXml(page);
  await selectDocument(page, 'Таймер 30 минут');
  await expect(page.getByTestId('activity-timers')).toHaveText('Таймер:PT30M');
  expect(await exportXml(page)).toBe(before);
  await page.getByRole('tab', { name: 'XML', exact: true }).click();
  const source = page.getByTestId('document-source');
  await source.fill((await source.inputValue()).replace('PT30M', 'PT45M').replace('Вода прозрачная', 'Вода проверена'));
  await page.getByTestId('document-apply-source').click();
  await selectDocument(page, 'Сообщение датчика');
  await expect(page.getByTestId('activity-messages')).toHaveText('Сообщение:Вода проверена');
  await selectDocument(page, 'Таймер 30 минут');
  await expect(page.getByTestId('activity-timers')).toHaveText('Таймер:PT45M');
  await page.getByRole('tab', { name: 'Диаграмма', exact: true }).click();
  await page.screenshot({ path: info.outputPath('referenced-definition-current-xml.png'), animations: 'disabled' });
});

test('Типизированные события, ссылки и сообщения задач: namespace, scope и metadata не подменяют сведения', async ({ page }) => {
  await loginTestAccount(page); await page.goto('/');
  const results = await page.evaluate(async () => {
    const { activityDetails } = await import('/src/activityDetails.ts');
    const schema = await (await fetch('/api/bpmn/schema')).json();
    const ns = 'http://www.omg.org/spec/BPMN/20100524/MODEL';
    const render = (roots: string, selected: string) => {
      const doc = new DOMParser().parseFromString(`<b:definitions xmlns:b="${ns}" xmlns:t="urn:local" xmlns:v="urn:foreign" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" targetNamespace="urn:local">${roots}<b:process id="p">${selected}</b:process></b:definitions>`, 'application/xml');
      return activityDetails(doc, Array.from(doc.getElementsByTagNameNS(ns, '*')).find(e => e.getAttribute('id') === 'chosen'), schema);
    };
    const message = '<b:message id="m" name="Вода прозрачная"/>';
    const event = (definition: string) => `<b:intermediateCatchEvent id="chosen">${definition}</b:intermediateCatchEvent>`;
    const inline = '<b:messageEventDefinition messageRef="t:m"/>';
    const referenced = '<b:eventDefinitionRef>t:global</b:eventDefinitionRef>';
    const definition = '<b:messageEventDefinition id="global" messageRef="t:m"/>';
    return {
      message: render(message, event(inline)),
      referenced: render(message + definition, event(referenced)),
      typed: render(message + '<b:rootElement id="global" xsi:type="b:tMessageEventDefinition" messageRef="t:m"/>', event(referenced)),
      signal: render('<b:signal id="s" name="Перегрев"/>', event('<b:signalEventDefinition signalRef="t:s"/>')),
      error: render('<b:error id="e" name="Недоступен датчик" errorCode="SENSOR_OFFLINE"/>', '<b:boundaryEvent id="chosen"><b:errorEventDefinition errorRef="t:e"/></b:boundaryEvent>'),
      escalation: render('<b:escalation id="e" name="Нужен техник" escalationCode="TECHNICIAN_REQUIRED"/>', '<b:intermediateThrowEvent id="chosen"><b:escalationEventDefinition escalationRef="t:e"/></b:intermediateThrowEvent>'),
      errorCode: render('<b:error id="e" errorCode="SENSOR_OFFLINE"/>', '<b:boundaryEvent id="chosen"><b:errorEventDefinition errorRef="t:e"/></b:boundaryEvent>'),
      escalationCode: render('<b:escalation id="e" name=" " escalationCode="TECHNICIAN_REQUIRED"/>', '<b:intermediateThrowEvent id="chosen"><b:escalationEventDefinition escalationRef="t:e"/></b:intermediateThrowEvent>'),
      errorId: render('<b:error id="unnamed_error"/>', '<b:boundaryEvent id="chosen"><b:errorEventDefinition errorRef="t:unnamed_error"/></b:boundaryEvent>'),
      timers: render('', event('<b:timerEventDefinition><b:timeCycle xsi:type="b:tFormalExpression">R3/PT5M</b:timeCycle></b:timerEventDefinition>')),
      date: render('', event('<b:timerEventDefinition><b:timeDate>2026-10-05T12:00:00Z</b:timeDate></b:timerEventDefinition>')),
      send: render(message, '<b:sendTask id="chosen" messageRef="t:m"/>'),
      receive: render(message, '<b:receiveTask id="chosen" messageRef="m"/>'),
      external: render(message, event(inline.replace('t:m', 'v:m'))),
      foreign: render(message.replaceAll('b:message ', 'v:message '), event(inline)),
      metadata: render('<b:extensionElements>' + message + '</b:extensionElements>', event(inline)),
      documentation: render('<b:documentation>' + message + '</b:documentation>', event(inline)),
      duplicate: render(message + message, event(inline)),
      wrongType: render('<b:signal id="m" name="Ложное сообщение"/>', event(inline)),
      wrongExplicitType: render('<b:message id="m" name="Ложное сообщение" xsi:type="b:tSignal"/>', event(inline)),
      wrongOwner: render('', event(message + inline)),
      externalDefinition: render(message + definition, event(referenced.replace('t:global', 'v:global'))),
      duplicateDefinition: render(message + definition + definition, event(referenced)),
      embeddedDefinition: render(message, event(definition + referenced)),
      metadataDefinition: render(message + '<b:extensionElements>' + definition + '</b:extensionElements>', event(referenced)),
      foreignDefinition: render(message, event(inline.replace('b:messageEventDefinition', 'v:messageEventDefinition'))),
      wrongDefinitionType: render(message + '<b:signal id="global" name="Not a definition"/>', event(referenced)),
      pollutedRef: render(message + definition, event('<b:eventDefinitionRef><v:text>t:global</v:text></b:eventDefinitionRef>')),
      pollutedTimer: render('', event('<b:timerEventDefinition><b:timeDuration>PT<v:text>30</v:text>M</b:timeDuration></b:timerEventDefinition>')),
    };
  });
  for (const key of ['message', 'referenced', 'typed', 'send', 'receive'] as const) expect(results[key].messages, key).toEqual(['Вода прозрачная']);
  expect(results.signal.signals).toEqual(['Перегрев']);
  expect(results.error.errors).toEqual(['Недоступен датчик']);
  expect(results.escalation.escalations).toEqual(['Нужен техник']);
  expect(results.errorCode.errors).toEqual(['SENSOR_OFFLINE']);
  expect(results.escalationCode.escalations).toEqual(['TECHNICIAN_REQUIRED']);
  expect(results.errorId.errors).toEqual(['unnamed_error']);
  expect(results.timers.timers).toEqual(['R3/PT5M']);
  expect(results.date.timers).toEqual(['2026-10-05T12:00:00Z']);
  for (const key of ['external', 'foreign', 'metadata', 'documentation', 'duplicate', 'wrongType', 'wrongExplicitType',
    'wrongOwner', 'externalDefinition', 'duplicateDefinition', 'metadataDefinition', 'foreignDefinition', 'wrongDefinitionType', 'pollutedRef'] as const) {
    expect(results[key].messages, key).toEqual([]);
  }
  // An inline definition still applies directly; the illegal reference to it adds no duplicate fact.
  expect(results.embeddedDefinition.messages).toEqual(['Вода прозрачная']);
  expect(results.pollutedTimer.timers).toEqual([]);
});
