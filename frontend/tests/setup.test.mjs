import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
test('страница использует русский язык', async () => {
  assert.match(await readFile(new URL('../index.html', import.meta.url), 'utf8'), /lang="ru"/);
});
