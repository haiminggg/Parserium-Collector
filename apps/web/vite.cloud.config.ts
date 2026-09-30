import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), {
    name: "parserium-cloud-entry",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => html.replace("/src/main.tsx", "/src/cloud/main.tsx")
        .replace("<title>Parserium Collector</title>", '<title>Parserium</title><meta name="robots" content="noindex, nofollow" />'),
    },
  }],
  build: { outDir: "dist-cloud", sourcemap: false },
  server: { host: "127.0.0.1", proxy: { "/api": "http://127.0.0.1:8787" } },
});
