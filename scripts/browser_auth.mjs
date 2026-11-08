// Explicit authentication for smoke scripts against an ordinary application.
// Provision accounts locally first; credentials and cookies stay in memory.

export function smokeCredentials(args = process.argv.slice(2), env = process.env) {
  if (args.includes('--create-test-account')) {
    throw new Error('Регистрация закрыта. Создайте аккаунт локально через backend.app.manage_users и задайте TEST_USERNAME/TEST_PASSWORD.');
  }
  if (args.length) throw new Error('Для входа smoke-проверки используйте только TEST_USERNAME/TEST_PASSWORD.');
  if (!env.TEST_USERNAME || !env.TEST_PASSWORD) {
    throw new Error('Нужны TEST_USERNAME и TEST_PASSWORD существующего аккаунта. Создайте его локально через backend.app.manage_users.');
  }
  return { username: env.TEST_USERNAME, password: env.TEST_PASSWORD };
}

export async function authenticateSmokePage(page, url, credentials, expect) {
  try {
    await page.goto(url);
    await expect(page.getByTestId('auth-page')).toBeVisible();
    await page.getByTestId('auth-username').fill(credentials.username);
    await page.getByTestId('auth-password').fill(credentials.password);
    const [response] = await Promise.all([
      page.waitForResponse(response => new URL(response.url()).pathname === '/api/auth/login'
        && response.request().method() === 'POST'),
      page.getByTestId('auth-submit').click(),
    ]);
    if (response.status() !== 200) {
      // Do not include request payloads, cookies, response tokens or credentials.
      throw new Error(`Вход smoke-проверки не выполнен: HTTP ${response.status()}.`);
    }
    await expect(page.getByTestId('description')).toBeVisible();
    await expect(page.getByTestId('auth-page')).toHaveCount(0);
    return { method: 'existing_account' };
  } catch (error) {
    // Playwright's failed fill() call log can contain its argument.
    const message = String(error.message || 'Ошибка входа').replaceAll(credentials.password, '[REDACTED]');
    throw new Error(message);
  }
}
