import { expect, test } from "@playwright/test";

import { mockDatabaseReady } from "./_helpers";

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
});
