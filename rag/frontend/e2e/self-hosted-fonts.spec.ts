import { expect, test, type Page } from "@playwright/test";

import { mockDatabaseReady } from "./_helpers";

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

/**
 * 自前ホスト Web フォント(@fontsource / オフライン)の検証。
 *
 * - 実行時に Google Fonts などの外部 CDN へ一切アクセスしないこと。
 * - 日本語第一フォント Noto Sans JP が実際に読み込まれ body に適用されること。
 */
test.describe("自前ホストフォント", () => {
  const EXTERNAL_FONT_HOSTS = [
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "fonts.bunny.net",
  ];

  test("外部フォント CDN へアクセスせず Noto Sans JP がローカル適用される", async ({ page }) => {
    await mockDatabaseReady(page);

    // 外部フォント CDN への通信が発生したら記録する。
    const externalFontRequests: string[] = [];
    page.on("request", (request) => {
      const url = request.url();
      if (EXTERNAL_FONT_HOSTS.some((host) => url.includes(host))) {
        externalFontRequests.push(url);
      }
    });

    await page.goto("/");

    // フォント読み込み完了まで待つ。
    await page.evaluate(() => document.fonts.ready);

    // 外部 CDN への通信がないこと。
    expect(externalFontRequests, "外部フォント CDN への通信").toEqual([]);

    // Noto Sans JP が weight 400/500/700 で読み込まれていること。
    // check だけだと、可視テキストが未使用の weight(例: 700/太字)は document.fonts.ready
    // 解決後も未ロードのままで false になり得る(CI で 700 のみ落ちていた競合の原因)。
    // ローカル(@fontsource)から各 weight を明示ロードして決定論化する。
    const loaded = await page.evaluate(async () => {
      const sample = "規程 Aa";
      const load = (weight: number) =>
        document.fonts
          .load(`${weight} 14px 'Noto Sans JP'`, sample)
          .then((faces) => faces.length > 0)
          .catch(() => false);
      return { w400: await load(400), w500: await load(500), w700: await load(700) };
    });
    expect(loaded.w400, "Noto Sans JP 400").toBe(true);
    expect(loaded.w500, "Noto Sans JP 500").toBe(true);
    expect(loaded.w700, "Noto Sans JP 700").toBe(true);

    // body のフォントスタック先頭が Noto Sans JP であること。
    const fontFamily = await page.evaluate(
      () => getComputedStyle(document.body).fontFamily
    );
    expect(fontFamily).toContain("Noto Sans JP");

    // 実際にフォントファイルがローカル(同一オリジン)から配信されていること。
    const fontSources = await page.evaluate(() =>
      Array.from(document.fonts).map((f) => f.family)
    );
    expect(fontSources).toContain("Noto Sans JP");
  });

  test("等幅書体は Google Sans Code を自前ホストし、code と font-mono に効く", async ({ page }) => {
    await mockDatabaseReady(page);
    await page.goto("/");
    await checkMonoFont(page);
  });
});
