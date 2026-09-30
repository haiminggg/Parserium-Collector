import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const dashboardStyles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");

function cssRule(selector: string): string {
  const start = dashboardStyles.indexOf(`${selector} {`);
  if (start === -1) return "";
  const bodyStart = dashboardStyles.indexOf("{", start) + 1;
  const bodyEnd = dashboardStyles.indexOf("}", bodyStart);
  return dashboardStyles.slice(bodyStart, bodyEnd);
}

describe("dashboard layout styles", () => {
  it("excludes checkbox and radio controls from the shared field height", () => {
    expect(dashboardStyles).toContain(
      'input:not([type="checkbox"]):not([type="radio"]),',
    );
  });

  it("keeps vertical scrolling inside the queue and hides the page scrollbar", () => {
    expect(cssRule("html")).toContain("scrollbar-width: none;");
    expect(cssRule(".queue-panel")).toContain("overflow-y: auto;");
    expect(cssRule(".queue-panel")).toContain("overflow-x: hidden;");
    expect(cssRule(".queue-panel")).toContain("scrollbar-width: thin;");
  });

  it("keeps content in flow and truncates every long document name", () => {
    expect(cssRule(".discovery-content")).toContain("grid-area: results;");
    expect(cssRule(".discovery-content")).toContain("display: flex;");
    expect(cssRule(".queue-list-region")).toContain("flex: 0 0 auto;");
    expect(cssRule(".queue-clear-completed")).toContain("margin-top: 0.5rem;");
    for (const selector of [
      ".result-title",
      ".queue-featured-title",
      ".queue-item-title",
      ".document-identity p:first-child",
      ".export-filename",
    ]) {
      expect(cssRule(selector), selector).toContain("overflow: hidden;");
      expect(cssRule(selector), selector).toContain("text-overflow: ellipsis;");
    }
  });

  it("uses the approved warm-light tokens and ruled geometry", () => {
    const root = cssRule(":root");
    expect(root).toContain("--parserium-canvas: #f7f5f0;");
    expect(root).toContain("--parserium-surface: #fcfbf8;");
    expect(root).toContain("--parserium-text: #171614;");
    expect(root).toContain("--parserium-accent: #bf380a;");
    expect(root).toContain("--parserium-success: #26783c;");
    expect(root).toContain("--parserium-error: #b7352a;");
    expect(cssRule(".parserium-header")).toContain("height: 72px;");
    expect(cssRule(".operations-grid")).toContain(
      "grid-template-columns: minmax(0, 2.25fr) minmax(20rem, 1fr);",
    );
    expect(cssRule(".documents-utility")).toContain("height: 66px;");
  });

  it("contains horizontal overflow inside results and disables nonessential motion", () => {
    expect(cssRule(".result-table")).toContain("overflow-x: auto;");
    expect(dashboardStyles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(dashboardStyles).toContain("animation-duration: 0.01ms !important;");
  });
});
