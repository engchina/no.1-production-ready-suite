import { expect, type Page } from "@playwright/test";

type Box = { x: number; y: number; width: number; height: number };

function overlaps(a: Box, b: Box) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

/**
 * 通知（Toast）の置き場所（#411）。主操作を覆わないよう、画面の上端の見出しの面に重ねる。
 * - desktop（md 以上）: PageHeader に重ね、ページの操作のすぐ左（PageHeader の上端 + 1rem）。
 *   ページの操作の左が狭ければ PageHeader の下、PageHeader が見えなければ画面の右上。どの場合もページの操作を覆わない。
 * - 375px（md 未満）: 上端のバーに重ねる（上端から 0.5rem、右端から 1rem）。メニューのボタンを覆わない。
 */
export async function expectToastStackAtTop(page: Page) {
  const region = page.getByRole("region", { name: "通知" });
  const [box, viewport] = await Promise.all([region.boundingBox(), page.viewportSize()]);
  expect(box).not.toBeNull();
  expect(viewport).not.toBeNull();
  const rem = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).fontSize));
  const mode = await region.getAttribute("data-toast-placement");

  if (viewport!.width < 768) {
    expect(mode).toBe("top-bar");
    expect(Math.abs(box!.y - rem / 2)).toBeLessThanOrEqual(1);
    expect(Math.abs(viewport!.width - rem - (box!.x + box!.width))).toBeLessThanOrEqual(1);
    const menu = page.getByTestId("nav-drawer-trigger");
    if (await menu.count()) expect(overlaps(box!, (await menu.boundingBox())!)).toBe(false);
    return;
  }

  expect(["page-header", "below-page-header", "top-right"]).toContain(mode);
  expect(box!.x + box!.width).toBeLessThanOrEqual(viewport!.width - rem + 1);
  const header = page.locator("[data-page-header]").first();
  const headerBox = (await header.count()) ? await header.boundingBox() : null;
  if (mode === "page-header") {
    expect(Math.abs(box!.y - (Math.max(0, headerBox!.y) + rem))).toBeLessThanOrEqual(1);
  } else if (mode === "below-page-header") {
    expect(Math.abs(box!.y - (headerBox!.y + headerBox!.height + rem))).toBeLessThanOrEqual(1);
  } else {
    expect(Math.abs(box!.y - rem)).toBeLessThanOrEqual(1);
  }
  const actions = header.locator("[data-page-header-actions]");
  if (headerBox && (await actions.count())) {
    expect(overlaps(box!, (await actions.boundingBox())!)).toBe(false);
  }
}
