import { expect, type Page } from "@playwright/test";

/** 画面内の全テキストと入力欄を走査し、局所指定やコード要素の字体漏れを検出する。 */
export async function expectLocalUiFonts(page: Page) {
  await page.evaluate(() => document.fonts.ready);
  const result = await page.evaluate(() => {
    const canonical = (font: string) => font.split(",").map((part) => part.trim().replaceAll('"', "")).join(",");
    const expected = getComputedStyle(document.body).fontFamily;
    const mismatches: { tag: string; className: string; font: string }[] = [];
    const codeFont = getComputedStyle(document.documentElement).getPropertyValue("--font-mono").trim();
    let checked = 0;
    for (const node of document.querySelectorAll("body *")) {
      if (!(node instanceof HTMLElement || node instanceof SVGElement)) continue;
      if (["SCRIPT", "STYLE", "NOSCRIPT", "OPTION"].includes(node.tagName)) continue;
      const style = getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" || !node.getClientRects().length) continue;
      const hasText = Array.from(node.childNodes).some(
        (child) => child.nodeType === Node.TEXT_NODE && child.textContent?.trim()
      );
      if (!hasText && !node.matches("input, textarea, select")) continue;
      checked += 1;
      if (canonical(style.fontFamily) !== canonical(expected) && canonical(style.fontFamily) !== canonical(codeFont)) {
        mismatches.push({ tag: node.tagName, className: node.getAttribute("class") ?? "", font: style.fontFamily });
      }
    }
    return { expected, codeFont, checked, mismatches };
  });
  expect(result.expected).toMatch(/^"Noto Sans JP", Roboto,/);
  expect(result.codeFont).toMatch(/^"Google Sans Code", "Noto Sans JP", "Roboto", monospace$/);
  expect(result.checked).toBeGreaterThan(0);
  expect(result.mismatches).toEqual([]);
}
