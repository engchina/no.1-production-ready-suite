import type { Page } from "@playwright/test";

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
