import { expect, test } from "./fixtures/mock-api";
import type { Page } from "@playwright/test";

import {
  CJK_FALLBACK_SAMPLES,
  FONT_MONO_FAMILY,
  FONT_SANS_PREFIX,
  loadCjkFallbackFonts,
  renderedCjkFallbackFonts,
} from "../../../platform/packages/ui/e2e/font-checks";

/** Noto Sans JP と同じ太さ（src/fonts.css）。 */
const WEIGHTS = [400, 500, 700] as const;

/** 等幅書体（--font-mono）が Google Sans Code で、code / .font-mono に効くことを確かめる。 */
async function checkMonoFont(page: Page) {
  const result = await page.evaluate(async () => {
    const code = document.createElement("code");
    code.textContent = "SELECT 1200000";
    const utility = document.createElement("span");
    utility.className = "font-mono";
    utility.textContent = "doc-0001";
    document.body.append(code, utility);
    const counts: number[] = [];
    for (const weight of [400, 500, 600, 700]) {
      const faces = await document.fonts.load(`${weight} 14px "Google Sans Code"`, "SELECT 1200000");
      counts.push(faces.filter((face) => face.status === "loaded").length);
    }
    const families = [getComputedStyle(code).fontFamily, getComputedStyle(utility).fontFamily];
    code.remove();
    utility.remove();
    return { counts, families };
  });
  expect(result.counts.every((count) => count > 0), "Google Sans Code 400/500/600/700").toBe(true);
  for (const family of result.families) {
    expect(family).toBe(FONT_MONO_FAMILY);
  }
}

// 書体は @fontsource の woff2 を同一 origin で配信し、外部フォント CDN に依存しない（#75）。
test("Noto Sans JP と等幅書体 Google Sans Code を自前ホストで適用する", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    if (/fonts\.(googleapis|gstatic)\.com|fonts\.bunny\.net/u.test(request.url())) external.push(request.url());
  });
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  const noto = await page.evaluate(async () =>
    (await document.fonts.load("400 14px 'Noto Sans JP'", "業務 Agent")).length > 0
  );
  expect(noto, "Noto Sans JP 400").toBe(true);
  expect(await page.evaluate(() => getComputedStyle(document.body).fontFamily)).toContain("Noto Sans JP");
  await checkMonoFont(page);
  expect(external, "外部フォント CDN への通信").toEqual([]);
});

// Noto Sans JP に無い字だけを、自前ホストの Noto Sans SC → TC → HK で補う（#3292）。字は合成の例。
test("JP に無い簡体字・繁体字・香港の字を Noto Sans SC・TC・HK で補う", async ({ page }) => {
  const external: string[] = [];
  page.on("request", (request) => {
    if (/fonts\.(googleapis|gstatic)\.com|fonts\.bunny\.net/u.test(request.url())) external.push(request.url());
  });
  await page.goto("/");
  await page.evaluate(() => document.fonts.ready);
  const bodyFont = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
  expect(bodyFont.slice(0, FONT_SANS_PREFIX.length)).toBe(FONT_SANS_PREFIX);
  for (const font of await loadCjkFallbackFonts(page, WEIGHTS)) {
    expect(font.count, `${font.family} ${font.weight}`).toBeGreaterThan(0);
  }
  const cdp = await page.context().newCDPSession(page);
  try {
    const rendered = await renderedCjkFallbackFonts(page, cdp);
    for (const sample of CJK_FALLBACK_SAMPLES) {
      for (const kind of ["sans", "mono"] as const) {
        const families = rendered[`${sample.id}-${kind}`];
        expect(families.length, `${sample.id}-${kind}`).toBeGreaterThan(0);
        expect(families.every((family) => family.startsWith(sample.family)), `${sample.id}-${kind}: ${families.join(", ")}`).toBe(true);
      }
    }
  } finally {
    await cdp.detach();
  }
  expect(external, "外部フォント CDN への通信").toEqual([]);
});
