// Local production acceptance: provision an isolated test identity, then use the real UI.
// Passwords are random, passed through stdin/env only, and never written to artifacts.
import { randomBytes } from 'node:crypto';
import { spawnSync, spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const username = `bpmn_check_${Date.now()}`;
const password = randomBytes(32).toString('base64url');
const provision = spawnSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-m',
  'backend.app.manage_users', 'create', username, '--password-stdin'], {
  cwd: root, input: `${password}\n`, encoding: 'utf8', windowsHide: true,
});
if (provision.status !== 0) throw new Error('Local test account provisioning failed.');
const run = spawn(process.execPath, ['scripts/live_ui_check.mjs'], {
  cwd: root, windowsHide: true, stdio: 'inherit',
  env: { ...process.env, TEST_USERNAME: username, TEST_PASSWORD: password,
    SMOKE_SOURCE_FILE: 'examples/full_bpmn_live_nested.txt',
    SMOKE_OUTPUT_DIR: `artifacts/full-bpmn/production-ui/${Date.now()}` },
});
run.on('error', () => { process.exitCode = 1; });
run.on('exit', code => { process.exitCode = code ?? 1; });
