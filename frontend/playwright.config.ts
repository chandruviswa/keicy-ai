import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end: a fresh `paypal_e2e` database (accounts only, empty catalogue), the real backend on
 * :8100 using the local Ollama model and the PayPal sandbox from backend/.env, and the real frontend
 * on :5174. Nothing is mocked; Ollama must be running and the machine needs internet for PayPal.
 */
const backendEnv = [
  "DB_NAME=paypal_e2e",
  "LLM_PROVIDERS=ollama",
  "PAYPAL_MODE=sandbox",
  "APP_ENV=e2e",
  "LOG_FORMAT=text",
  "LOG_LEVEL=WARNING",
  "JWT_SECRET=e2e-only-secret-not-for-production",
  "UV_LINK_MODE=copy",
].join(" ");

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:5174", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    // Exact names, so macOS "._*.spec.ts" metadata files on exFAT drives are never picked up.
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1366, height: 900 } }, testMatch: /\/journey\.spec\.ts$/ },
    { name: "mobile", use: { ...devices["Pixel 7"] }, testMatch: /\/responsive\.spec\.ts$/ },
    // iPad-sized viewport in Chromium (the iPad device profile would need WebKit installed).
    { name: "tablet", use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 }, hasTouch: true }, testMatch: /\/responsive\.spec\.ts$/ },
    { name: "wide", use: { ...devices["Desktop Chrome"], viewport: { width: 1920, height: 1080 } }, testMatch: /\/responsive\.spec\.ts$/ },
  ],
  webServer: [
    {
      command: `cd ../backend && ${backendEnv} uv run python -m scripts.reset_db paypal_e2e && ${backendEnv} uv run uvicorn app.main:app --port 8100`,
      url: "http://localhost:8100/api/v1/health",
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: "API_TARGET=http://localhost:8100 npx vite --port 5174 --strictPort",
      url: "http://localhost:5174",
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
