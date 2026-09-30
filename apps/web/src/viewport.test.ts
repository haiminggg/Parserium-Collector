import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(resolve(process.cwd(), "src", path), "utf8");

describe("viewport sizing", () => {
  it("lets Collect grow beyond the viewport instead of squeezing results", () => {
    const css = read("workspace/workspace.css");
    expect(css).not.toMatch(/height: calc\(100dvh - (80|112)px\)/);
    expect(css).toContain("grid-template-rows: minmax(8.75rem, auto) auto minmax(28rem, auto) auto;");
    expect(css).not.toContain("scrollbar-width: thin !important;");
    expect(css).not.toContain("scrollbar-gutter: stable;");
    expect(css).not.toContain("height: min(40rem, 75dvh)");
    expect(read("workspace/document-motion.css")).not.toContain(".operations-grid:has(.document-motion)");
  });

  it("compacts sign in on shorter screens without disabling scrolling", () => {
    const css = read("auth/login.css");
    expect(css).toContain("@media (max-height: 850px)");
    expect(css).toContain("overflow-y: auto;");
  });

  it("hides native scrollbars including portalled dialogs", () => {
    const css = read("styles.css");
    expect(css).toContain("scrollbar-width: none !important;");
    expect(css).toContain("*::-webkit-scrollbar");
  });
});
