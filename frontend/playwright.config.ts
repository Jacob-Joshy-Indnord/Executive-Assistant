/**
 * Story 7.2: one automatic run through everything, against the demonstration.
 *
 * It starts both ends itself, uses the Chromium already present in the build environment
 * (PLAYWRIGHT_BROWSERS_PATH), and moves the demonstration's clock through the bar, so it never
 * waits on the real clock or the network.
 */
import { defineConfig } from "@playwright/test";

const BACKEND_PORT = Number(process.env.EA_E2E_BACKEND_PORT ?? 8010);
const FRONTEND_PORT = Number(process.env.EA_E2E_FRONTEND_PORT ?? 3010);
const BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
const FRONTEND_URL = `http://127.0.0.1:${FRONTEND_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 10 * 60_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    baseURL: FRONTEND_URL,
    browserName: "chromium",
    locale: "en-GB",
    timezoneId: "UTC",
    viewport: { width: 1280, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `${process.env.PYTHON ?? "python3"} -m standins.demo`,
      cwd: "../backend",
      url: `${BACKEND_URL}/api/demo`,
      env: { EA_DEMO_HOST: "127.0.0.1", EA_DEMO_PORT: String(BACKEND_PORT) },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${FRONTEND_PORT}`,
      url: `${FRONTEND_URL}/assistant`,
      env: { EA_BACKEND_URL: BACKEND_URL },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
