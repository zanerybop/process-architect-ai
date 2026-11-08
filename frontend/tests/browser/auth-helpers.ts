import { expect, type Page } from '@playwright/test';
import { randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

export function testCredentials() {
  return {
    username: `browser_${randomBytes(8).toString('hex')}`,
    password: randomBytes(24).toString('base64url'),
  };
}

// Provision locally into the isolated fixture database, then use normal login.
// There is no account-creation HTTP endpoint or production authentication bypass.
export function provisionTestAccount() {
  const credentials = testCredentials();
  const root = resolve('..');
  const python = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
  const result = spawnSync(python, ['-m', 'tests.browser_server', '--provision-account'], {
    cwd: root, env: process.env, input: JSON.stringify(credentials), encoding: 'utf8', timeout: 15_000,
  });
  if (result.status !== 0) {
    // Never echo stdin or captured output: credentials are private even in tests.
    throw new Error(`Local browser fixture provisioning failed (exit ${result.status}).`);
  }
  return credentials;
}

export async function loginTestAccount(page: Page) {
  const credentials = provisionTestAccount();
  const response = await page.request.post('/api/auth/login', {
    data: credentials, headers: { 'X-Requested-With': 'ProcessArchitect' },
  });
  expect(response.status()).toBe(200);
  const session = await response.json();
  expect(session.user.username).toBe(credentials.username);
  expect(session.csrf_token).toEqual(expect.any(String));
  return { ...credentials, session };
}

export async function csrfHeaders(page: Page) {
  const response = await page.request.get('/api/auth/session');
  expect(response.status()).toBe(200);
  const session = await response.json();
  expect(session.user).not.toBeNull();
  return { 'X-CSRF-Token': session.csrf_token };
}

export async function selectExample(page: Page, id: string) {
  const select = page.getByTestId('example-select');
  await select.click();
  const input = select.getByRole('combobox');
  // Use the active accessible option: a virtual row can be detached while the
  // list scrolls, between locator resolution and a mouse click.
  let chosen = false;
  for (let index = 0; index < 30; index += 1) {
    const active = await input.getAttribute('aria-activedescendant');
    if (active && await page.locator(`[id="${active}"]`).textContent() === id) { chosen = true; break; }
    await input.press('ArrowDown');
  }
  expect(chosen, `Пример ${id} должен быть доступен с клавиатуры`).toBe(true);
  await input.press('Enter');
}
