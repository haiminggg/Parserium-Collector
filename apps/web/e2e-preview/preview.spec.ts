import { expect, test } from "@playwright/test";

for (const width of [1366, 390, 320]) {
  test(`static UI preview works without API at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 800 });
    const requests: string[] = [];
    const errors: string[] = [];
    page.on("request", (request) => {
      if (["fetch", "xhr"].includes(request.resourceType())) requests.push(request.url());
    });
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Continue with Google" })).toBeDisabled();
    await page.getByRole("button", { name: "Explore the preview" }).click();
    await page.getByRole("textbox", { name: /Search query/ }).fill("Annual reports");
    await expect(page.getByRole("button", { name: "Discover" })).toBeDisabled();
    await page.getByRole("button", { name: "Collection settings" }).click();
    await expect(page.getByText("Discovery filters")).toBeVisible();
    await page.getByRole("button", { name: "Close discovery filters" }).click();
    for (const destination of ["Documents", "Activity", "Collect"]) {
      await page.getByRole("button", { name: destination, exact: true }).click();
    }
    await expect(page.getByRole("textbox", { name: /Search query/ })).toHaveValue("Annual reports");
    expect(await page.locator(".preview-workspace").evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
    expect(await page.locator(".preview-workspace").evaluate((node) => getComputedStyle(node).scrollbarWidth)).toBe("none");
    expect(requests).toEqual([]);
    expect(errors).toEqual([]);
  });
}
