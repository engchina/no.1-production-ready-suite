import { expect, type Locator } from "@playwright/test";

export async function expectCompactSortHeaders(container: Locator) {
  const buttons = container.locator('[data-button-layout="sort"]');
  expect(await buttons.count()).toBeGreaterThan(0);
  for (const button of await buttons.all()) {
    const metrics = await button.evaluate((node) => ({
      rootFontSize: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
      touch: matchMedia("(max-width: 639px), (pointer: coarse)").matches,
      clipped: node.scrollWidth > node.clientWidth,
      labelClipped: Array.from(node.querySelectorAll("span")).some(label => label.scrollWidth > label.clientWidth),
    }));
    await expect(button).toHaveCSS("font-size", `${metrics.rootFontSize * 0.75}px`);
    await expect(button).toHaveCSS("font-weight", "600");
    await expect(button).toHaveCSS("height", metrics.touch ? "44px" : "32px");
    expect(metrics.clipped).toBe(false);
    expect(metrics.labelClipped).toBe(false);
  }
}
