import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e-preview",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:4189", headless: true, reducedMotion: "reduce" },
  webServer: {
    command: "node node_modules/vite/bin/vite.js preview --config vite.preview.config.ts --host 127.0.0.1 --port 4189 --strictPort",
    url: "http://127.0.0.1:4189", reuseExistingServer: false,
  },
});
