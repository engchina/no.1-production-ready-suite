import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const backendPort = 8042;
const frontendPort = 3042;
const externalToolsPort = 8052;
const backendUrl = `http://127.0.0.1:${backendPort}`;
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const externalToolsUrl = `http://127.0.0.1:${externalToolsPort}`;
const backendCwd = fileURLToPath(new URL("../backend", import.meta.url));
const frontendCwd = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  use: {
    baseURL: frontendUrl,
    trace: "on-first-retry",
  },
  webServer: [
    {
      command: `node ./e2e/fixtures/external-tools-server.mjs --port ${externalToolsPort}`,
      cwd: frontendCwd,
      url: `${externalToolsUrl}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: backendCwd,
      url: `${backendUrl}/api/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `BACKEND_URL=${backendUrl} npm run dev -- --host 127.0.0.1 --port ${frontendPort}`,
      cwd: frontendCwd,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
