import { test, expect } from '@playwright/test';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ids = ['P01', 'P02', 'P03', 'P04', 'P05', 'P06', 'P10', 'P11', 'P12', 'P13'];
for (const id of ids) {
  test(`${id}: реальный импорт, правки и повторное открытие`, async ({ page }) => {
    const xml = readFileSync(resolve(`../artifacts/m1/${id}/generated.bpmn`), 'utf8');
    await page.goto('/harness.html');
    await page.waitForFunction(() => Boolean((window as any).modeler));
    const result = await page.evaluate(async (input) => {
      const modeler = (window as any).modeler;
      const { warnings } = await modeler.importXML(input);
      modeler.get('canvas').zoom('fit-viewport');
      const registry = modeler.get('elementRegistry');
      const node = registry.getAll().find((el: any) => el.type.endsWith('Task') || el.type === 'bpmn:Task');
      const oldX = node.x;
      modeler.get('selection').select(node);
      modeler.get('modeling').updateProperties(node, { name: 'Правка аналитика: Проверить & подтвердить <2026>' });
      modeler.get('modeling').moveShape(node, { x: 40, y: 15 });
      const changedX = node.x;
      const saved = (await modeler.saveXML({ format: true })).xml;
      await modeler.importXML(saved);
      const again = modeler.get('elementRegistry').get(node.id);
      return { warnings: warnings.map((w: any) => w.message), id: node.id, oldX, changedX,
        actualX: again.x, name: again.businessObject.name, xml: saved };
    }, xml);
    expect(result.warnings).toEqual([]);
    expect(result.changedX).toBe(result.oldX + 40);
    expect(result.actualX).toBe(result.changedX);
    expect(result.name).toBe('Правка аналитика: Проверить & подтвердить <2026>');
    mkdirSync(resolve('../artifacts/browser'), { recursive: true });
    writeFileSync(resolve(`../artifacts/browser/${id}-edited.bpmn`), result.xml);
    if (id === 'P05' || id === 'P10') await page.screenshot({ path: resolve(`../artifacts/browser/${id}.png`) });
  });
}
