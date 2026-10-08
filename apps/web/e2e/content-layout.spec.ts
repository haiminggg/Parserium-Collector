import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("places search feedback above document results", async ({ page }) => {
  await page.setViewportSize({ width: 1_440, height: 900 });
  await page.setContent(`
    <style>* { box-sizing: border-box; }</style>
    <main class="operations-grid">
      <div class="discovery-hero">Search less. Find the right documents.</div>
      <div class="discovery-command-frame">Search controls</div>
      <div class="discovery-content">
        <div class="discovery-feedback">
          <div role="alert" style="min-height: 74px; padding: 16px;">Search failed</div>
        </div>
        <section class="discovery-results">
          <div class="result-toolbar">Discovered documents</div>
          <div class="result-table">Results</div>
        </section>
      </div>
      <section class="queue-panel">Collection</section>
      <footer class="documents-utility">Stored documents</footer>
    </main>
  `);
  await page.addStyleTag({ path: resolve(process.cwd(), "src/styles.css") });

  const content = page.locator(".discovery-content");
  const feedback = page.locator(".discovery-feedback");
  const results = page.locator(".discovery-results");
  const [feedbackBox, resultsBox] = await Promise.all([
    feedback.boundingBox(),
    results.boundingBox(),
  ]);

  await expect(content).toHaveCSS("grid-area", "results");
  await expect(content).toHaveCSS("display", "flex");
  expect(feedbackBox).not.toBeNull();
  expect(resultsBox).not.toBeNull();
  expect(resultsBox!.y).toBeGreaterThanOrEqual(feedbackBox!.y + feedbackBox!.height - 0.5);
});
