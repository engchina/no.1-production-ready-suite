import { expect, type Page } from "@playwright/test";

import {
  CJK_FALLBACK_SAMPLES,
  DEFERRED_CJK_FAMILIES,
  FONT_MONO_FAMILY,
  FONT_SANS_PREFIX,
  firstRenderFontFaces,
  initialStylesheetFontFaces,
  loadCjkFallbackFonts,
  renderedCjkFallbackFonts,
} from "../../../../../platform/packages/ui/e2e/font-checks";

export { recordFirstRenderFontFaces } from "../../../../../platform/packages/ui/e2e/font-checks";

/** 自前ホストの書体の太さ（src/main.tsx・src/fonts-cjk.css。Noto Sans JP と SC・TC・HK で同じ）。 */
export const LOCAL_FONT_WEIGHTS = [400, 500, 600, 700] as const;

/**
 * Noto Sans SC・TC・HK の @font-face が主の CSS に無く、最初の描画の後に読まれることを確かめる（#3305）。
 * `recordFirstRenderFontFaces(page)` を `page.goto` の前に呼んでおく。
 */
export async function expectDeferredCjkFontFaces(page: Page) {
  const firstRender = await firstRenderFontFaces(page);
  expect(firstRender.families).toContain("Noto Sans JP");
  const initial = await initialStylesheetFontFaces(page);
  // dev サーバの index.html は CSS の <link> を持たない（preview・本番だけ主の CSS を確かめられる）。
  if (initial.hrefs.length > 0) expect(initial.families).toContain("Noto Sans JP");
  for (const family of DEFERRED_CJK_FAMILIES) {
    expect(firstRender.families, `最初の描画の CSS に ${family}`).not.toContain(family);
    expect(initial.families, `index.html の CSS に ${family}`).not.toContain(family);
  }
}

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
  expect(result.expected.slice(0, FONT_SANS_PREFIX.length)).toBe(FONT_SANS_PREFIX);
  // カスタムプロパティの値は宣言のまま（引用符つきの "Roboto"）で返るため、引用符を外して比べる。
  expect(result.codeFont.replaceAll('"Roboto"', "Roboto")).toBe(FONT_MONO_FAMILY);
  expect(result.checked).toBeGreaterThan(0);
  expect(result.mismatches).toEqual([]);
}

/**
 * Noto Sans JP に無い字（合成の例）だけを、自前ホストの Noto Sans SC → TC → HK で補うことを確かめる（#3292）。
 * 各太さの読み込みと、本文・等幅で Chromium が実際に使った書体を見る。
 */
export async function expectLocalCjkFallbackFonts(page: Page) {
  for (const font of await loadCjkFallbackFonts(page, LOCAL_FONT_WEIGHTS)) {
    expect(font.count, `${font.family} ${font.weight}`).toBeGreaterThan(0);
  }
  const cdp = await page.context().newCDPSession(page);
  try {
    const rendered = await renderedCjkFallbackFonts(page, cdp);
    for (const sample of CJK_FALLBACK_SAMPLES) {
      for (const kind of ["sans", "mono"] as const) {
        const families = rendered[`${sample.id}-${kind}`];
        expect(families.length, `${sample.id}-${kind}`).toBeGreaterThan(0);
        expect(
          families.every((family) => family.startsWith(sample.renderedFamily)),
          `${sample.id}-${kind}: ${families.join(", ")}`
        ).toBe(true);
      }
    }
  } finally {
    await cdp.detach();
  }
}
