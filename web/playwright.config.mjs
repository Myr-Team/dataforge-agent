import { defineConfig } from "playwright/test";


const port = Number(process.env.DF_PLAYWRIGHT_PORT || 4173);
const baseURL = `http://127.0.0.1:${port}`;
const reuseExistingServer = process.env.DF_PLAYWRIGHT_REUSE_SERVER === "1";
const loopbackBypass = new Set(
  String(process.env.NO_PROXY || process.env.no_proxy || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean),
);
loopbackBypass.add("127.0.0.1");
loopbackBypass.add("localhost");
const noProxy = [...loopbackBypass].join(",");
process.env.NO_PROXY = noProxy;
process.env.no_proxy = noProxy;


export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: `npm run preview -- --port ${port}`,
    url: baseURL,
    reuseExistingServer,
    timeout: 30_000,
  },
});
