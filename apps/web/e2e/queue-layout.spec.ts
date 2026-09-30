import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

test("truncates long queue names without colliding with queue actions", async ({ page }) => {
  await page.setViewportSize({ width: 621, height: 1_476 });
  await page.setContent(`
    <style>* { box-sizing: border-box; }</style>
    <main id="drawer-shell" style="width: 563px; height: 760px; overflow: hidden;">
      <section class="queue-panel queue-panel-embedded">
        <div class="queue-list-region">
          <div class="queue-list">
            <article class="queue-item">
              <div class="queue-item-main">
                <span class="queue-index">1</span>
                <span class="file-glyph">DOCX</span>
                <div class="queue-item-copy">
                  <p class="queue-item-title" tabindex="0">
                    Recommended Actions for Facilities with Aboveground Storage Tanks and Additional Compliance Materials.docx
                  </p>
                  <p class="machine-data">181418 of 181418 bytes (100%)</p>
                </div>
                <span class="queue-status">Stored</span>
              </div>
            </article>
            <article class="queue-item">
              <div class="queue-item-main">
                <span class="queue-index">2</span>
                <span class="file-glyph">PDF</span>
                <div class="queue-item-copy">
                  <p class="queue-item-title" tabindex="0">Quarterly investment report.pdf</p>
                  <p class="machine-data">2048 of 2048 bytes (100%)</p>
                </div>
                <span class="queue-status">Stored</span>
              </div>
            </article>
            <article class="queue-item">
              <div class="queue-item-main">
                <span class="queue-index">3</span>
                <span class="file-glyph">DOCX</span>
                <div class="queue-item-copy">
                  <p class="queue-item-title" tabindex="0">Failed compliance report.docx</p>
                  <p class="machine-data">0 bytes downloaded</p>
                </div>
                <span class="queue-status">Failed</span>
              </div>
              <p class="queue-error">The document server rejected the request.</p>
            </article>
          </div>
        </div>
        <button class="queue-clear-completed" type="button">Clear completed</button>
      </section>
    </main>
  `);
  await page.addStyleTag({ path: resolve(process.cwd(), "src/styles.css") });

  const shell = page.locator("#drawer-shell");
  const item = page.locator(".queue-item");
  const title = page.locator(".queue-item-title").first();
  const lastItem = page.locator(".queue-item").last();
  const clear = page.locator(".queue-clear-completed");
  const [shellBox, itemBox, lastItemBox, clearBox] = await Promise.all([
    shell.boundingBox(),
    item.first().boundingBox(),
    lastItem.boundingBox(),
    clear.boundingBox(),
  ]);

  expect(shellBox).not.toBeNull();
  expect(itemBox).not.toBeNull();
  expect(lastItemBox).not.toBeNull();
  expect(clearBox).not.toBeNull();
  const shellRight = shellBox!.x + shellBox!.width;
  const itemRight = itemBox!.x + itemBox!.width;
  expect(itemRight).toBeLessThanOrEqual(shellRight + 0.5);
  expect(clearBox!.y).toBeGreaterThanOrEqual(lastItemBox!.y + lastItemBox!.height - 0.5);
  await expect(title).toHaveCSS("overflow-x", "hidden");
  await expect(title).toHaveCSS("text-overflow", "ellipsis");
  expect(
    await title.evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBe(true);
});
