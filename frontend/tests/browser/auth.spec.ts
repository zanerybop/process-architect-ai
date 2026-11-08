import { expect, test } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { csrfHeaders, loginTestAccount, provisionTestAccount, selectExample } from './auth-helpers';

const artifacts = resolve('../artifacts/login-only/browser-ui');

test.beforeEach(() => mkdirSync(artifacts, { recursive: true }));

test('Только вход: регистрация недоступна, сессия переживает reload, выход закрывает редактор', async ({ page }) => {
  const credentials = provisionTestAccount();
  await page.goto('/');
  await expect(page.getByTestId('auth-page')).toBeVisible();
  await expect(page.getByTestId('description')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Создать аккаунт', exact: true })).toHaveCount(0);
  await expect(page.getByText(/создать аккаунт|зарегистрироваться/i)).toHaveCount(0);
  const removedRegistration = await page.request.post('/api/auth/register', {
    data: credentials, headers: { 'X-Requested-With': 'ProcessArchitect' },
  });
  expect(removedRegistration.status()).toBe(404);
  expect((await (await page.request.get('/api/auth/session')).json()).user).toBeNull();
  await expect(page.locator('.auth-card')).toHaveCSS('opacity', '1');
  await page.screenshot({ path: resolve(artifacts, 'login.png') });
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    await expect(page.getByTestId('auth-submit')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: resolve(artifacts, `login-${viewport.width}.png`), fullPage: true });
  }
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByTestId('auth-username').fill(credentials.username);
  await page.getByTestId('auth-password').fill(credentials.password);
  await page.getByTestId('auth-password').press('Enter');
  await expect(page.getByTestId('description')).toBeVisible();
  await expect(page.getByTestId('account-menu')).toContainText(credentials.username);
  const cookies = await page.context().cookies();
  const session = cookies.find(cookie => cookie.name === 'process_architect_session');
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe('Lax');
  await page.reload();
  await expect(page.getByTestId('account-menu')).toContainText(credentials.username);
  await page.getByTestId('account-menu').click();
  await page.getByTestId('logout').click();
  await expect(page.getByTestId('auth-page')).toBeVisible();
  expect((await (await page.request.get('/api/auth/session')).json()).user).toBeNull();
  await page.getByTestId('auth-username').fill(credentials.username);
  await page.getByTestId('auth-password').fill(credentials.password);
  await page.getByTestId('auth-submit').click();
  await expect(page.getByTestId('account-menu')).toContainText(credentials.username);
  await expect(page.getByTestId('auth-page')).toHaveCount(0);
});

test('Неверный пароль оставляет форму входа и понятную ошибку', async ({ page }) => {
  const account = await loginTestAccount(page);
  const response = await page.request.post('/api/auth/logout', { headers: await csrfHeaders(page) });
  expect(response.ok()).toBe(true);
  await page.goto('/');
  await page.getByTestId('auth-username').fill(account.username);
  await page.getByTestId('auth-password').fill('deliberately-wrong-password');
  await page.getByTestId('auth-submit').click();
  await expect(page.getByTestId('auth-page')).toBeVisible();
  await expect(page.getByRole('alert')).toContainText(/логин|парол/i);
  await expect(page.getByTestId('auth-submit')).toBeEnabled();
  await expect(page.getByTestId('description')).toHaveCount(0);
});

test('Отозванная сессия возвращает вход и сохраняет правку до повторного входа', async ({ page }) => {
  const account = await loginTestAccount(page);
  await page.goto('/');
  await selectExample(page, 'P01');
  const source = await page.getByTestId('description').inputValue();
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('export')).toBeEnabled();
  await page.locator('.djs-element[data-element-id="p01_check"]').click();
  await page.getByTestId('element-name').fill('Правка перед завершением сессии');
  await page.getByTestId('rename').click();
  const response = await page.request.post('/api/auth/logout', { headers: await csrfHeaders(page) });
  expect(response.ok()).toBe(true);
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('auth-page')).toBeVisible();
  await expect(page.getByTestId('auth-page')).not.toContainText(/<html>|<!doctype>/i);
  expect((await (await page.request.get('/api/auth/session')).json()).user).toBeNull();
  await page.getByTestId('auth-username').fill(account.username);
  await page.getByTestId('auth-password').fill(account.password);
  await page.getByTestId('auth-submit').click();
  await expect(page.getByTestId('auth-page')).toHaveCount(0);
  await expect(page.getByTestId('description')).toHaveValue(source);
  await expect(page.getByTestId('element-name')).toHaveValue('Правка перед завершением сессии');
  await expect(page.getByTestId('export')).toBeDisabled();
  await page.getByTestId('validate').click();
  await expect(page.getByTestId('export')).toBeEnabled();
});

for (const reducedMotion of ['no-preference', 'reduce'] as const) {
  test(`Ожидание генерации показывает живой таймер (${reducedMotion})`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion });
    await loginTestAccount(page);
    await page.goto('/');
    await selectExample(page, 'P01');
    let release: () => void = () => {};
    const pending = new Promise<void>(resolve => { release = resolve; });
    let progressPolls = 0;
    await page.route('**/api/generation-progress/*', async route => {
      progressPolls++;
      await route.fulfill({ json: { phase: progressPolls === 1 ? 'model' : 'repair', attempt: 2 } });
    });
    await page.route('**/api/generate', async route => {
      await pending;
      await route.continue();
    });
    try {
      await page.getByTestId('generate').click();
      const progress = page.getByTestId('generation-progress');
      await expect(progress).toBeVisible();
      await expect(page.getByTestId('generate')).toBeDisabled();
      const elapsed = page.getByTestId('generation-elapsed');
      const initial = await elapsed.innerText();
      await expect(elapsed).not.toHaveText(initial, { timeout: 5_000 });
      await expect(progress).toContainText('попытка 2', { timeout: 7_000 });
      expect(parseInt(await elapsed.innerText(), 10)).toBeGreaterThanOrEqual(3);
      await expect(progress).not.toContainText(/\d+\s?%/);
      if (reducedMotion === 'reduce') {
        const durations = await progress.locator('*').evaluateAll(elements => elements.flatMap(element => {
          const style = getComputedStyle(element);
          return style.animationName === 'none' ? [] : style.animationDuration.split(',').map(value => parseFloat(value));
        }));
        expect(durations.every(duration => duration <= 0.01)).toBe(true);
      }
      await page.screenshot({ path: resolve(artifacts, `loading-${reducedMotion}.png`) });
      await page.setViewportSize({ width: 1280, height: 800 });
      await expect(progress).toBeVisible();
      await page.screenshot({ path: resolve(artifacts, `loading-${reducedMotion}-1280.png`) });
    } finally {
      release();
    }
    await expect(page.getByTestId('generation-progress')).toHaveCount(0);
    await expect(page.locator('.djs-element[data-element-id="p01_check"]')).toBeVisible();
  });
}

test('Ant Select доступен с клавиатуры и аккаунты имеют отдельную историю', async ({ page, browser }) => {
  await loginTestAccount(page);
  await page.goto('/');
  const examples = page.getByTestId('example-select').getByRole('combobox');
  await examples.focus();
  await examples.press('ArrowDown');
  await examples.press('Enter');
  await expect(page.getByTestId('description')).not.toHaveValue('');
  await page.getByTestId('generate').click();
  await expect(page.getByTestId('export')).toBeEnabled();
  const ownHistory = await (await page.request.get('/api/generations')).json();
  const records = Array.isArray(ownHistory) ? ownHistory : ownHistory.items;
  expect(records.length).toBe(1);
  await page.reload();
  await expect(page.getByTestId('history-select')).toBeVisible();
  const secondContext = await browser.newContext({ baseURL: 'http://127.0.0.1:5173' });
  try {
    const secondPage = await secondContext.newPage();
    await loginTestAccount(secondPage);
    const forbidden = await secondPage.request.get(`/api/generations/${records[0].id}`);
    expect(forbidden.status()).toBe(404);
    await secondPage.goto('/');
    await expect(secondPage.getByTestId('description')).toBeVisible();
    await expect(secondPage.getByTestId('history-select')).toHaveCount(0);
    const foreignHistory = await (await secondPage.request.get('/api/generations')).json();
    expect(Array.isArray(foreignHistory) ? foreignHistory : foreignHistory.items).toEqual([]);
  } finally {
    await secondContext.close();
  }
});
