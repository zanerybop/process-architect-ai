import { fileURLToPath } from 'node:url';
import base from '../playwright.config';

// Explicit integration run against an existing live artifact; never calls an LLM.
const frontend = fileURLToPath(new URL('../', import.meta.url));
export default {
  ...base,
  testDir: fileURLToPath(new URL('./live', import.meta.url)),
  outputDir: fileURLToPath(new URL('../../artifacts/full-bpmn-ui/live-test-results', import.meta.url)),
  reporter: [['list'], ['json', { outputFile: '../artifacts/full-bpmn-ui/live-results.json' }]],
  webServer: (Array.isArray(base.webServer) ? base.webServer : [base.webServer]).map((server) => ({ ...server, cwd: frontend })),
};
