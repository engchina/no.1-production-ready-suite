import { expect, test } from "./fixtures/mock-api";
import type { Page } from "@playwright/test";

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
    expect(family).toBe('"Google Sans Code", "Noto Sans JP", Roboto, monospace');
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
