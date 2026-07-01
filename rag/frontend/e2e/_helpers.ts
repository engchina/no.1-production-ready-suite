import { expect, type Page } from "@playwright/test";

/**
 * DB ゲート用の共通モック。
 * 設定ページ以外を開く前に呼ばれる `/api/ready/database` を「利用可能」にして、
 * ゲートに塞がれず本来のページを描画させる。
 */
export const DB_STATUS_OK = {
  data: { status: "ok", check: "ok", detail: null },
  error_messages: [],
  warning_messages: [],
};

/** `/api/ready/database` を ok 応答にして DB ゲートを通過させる。 */
export async function mockDatabaseReady(page: Page): Promise<void> {
  await page.route("**/api/ready/database", (route) => route.fulfill({ json: DB_STATUS_OK }));
}

/**
 * ページ全体が横スクロール(崩れ)していないことを検証する。
 *
 * `documentElement` だけでなく **`main`(`overflow-y-auto` で overflow-x も auto になる
 * スクロール領域)の内部はみ出し**も検査する。広いテーブルの `min-w-[…]` がグリッド子の
 * `min-w-0` 欠落でカラム幅を押し広げると、`main` が横スクロールを内部吸収してしまい
 * `documentElement` 基準のチェックだけでは見逃すため(知識ベース管理ページの崩れの実例)。
 * テーブル等の意図的な横スクロールは各自の `overflow-x-auto` の箱に閉じ込める前提。
 *
 * `expect.poll` で短時間リトライし、サイドバー折りたたみ等の **UI 遷移中の一過性のはみ出し**は
 * 吸収する(例: viewport を desktop→375 にリサイズした直後の width transition 200ms)。
 * 静的な実バグ(グリッド崩れ・scroll container の伝播)は沈静後も残るため確実に検出する。
 */
export async function expectNoPageOverflow(page: Page): Promise<void> {
  const measure = () =>
    page.evaluate(() => {
      const root = document.documentElement;
      const main = document.querySelector("main");
      return Math.max(
        root.scrollWidth - root.clientWidth,
        main ? main.scrollWidth - main.clientWidth : 0
      );
    });
  // 1px はスクロールバー/小数丸めの許容。遷移沈静まで最大 2s リトライ。
  await expect
    .poll(measure, { message: "ページ全体(documentElement / main)の横はみ出し", timeout: 2000 })
    .toBeLessThanOrEqual(1);
}

/** main を末尾までスクロールしたとき、実コンテンツの後ろに空白が残らないことを検証する。 */
export async function expectMainScrollEndsAtContent(page: Page): Promise<void> {
  const main = page.getByRole("main", { name: "メイン領域" });
  await expect
    .poll(
      () =>
        main.evaluate((element) => {
          const content = element.firstElementChild;
          if (!content) return Number.POSITIVE_INFINITY;
          element.scrollTo({ top: element.scrollHeight, left: 0, behavior: "auto" });
          return Math.max(
            0,
            element.getBoundingClientRect().bottom - content.getBoundingClientRect().bottom
          );
        }),
      { message: "main の末尾に実コンテンツを超える空白がないこと", timeout: 2000 }
    )
    .toBeLessThanOrEqual(1);
}
