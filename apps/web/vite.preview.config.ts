import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), {
    name: "isolated-ui-preview-entry",
    transformIndexHtml: {
      order: "pre",
      handler: (html) => html.replace("/src/main.tsx", "/src/preview/main.tsx")
        .replace("<title>Parserium Collector</title>", "<title>Parserium UI Preview</title><meta name=\"robots\" content=\"noindex, nofollow\" />"),
    },
  }],
  build: { outDir: "dist-preview", sourcemap: false },
});
