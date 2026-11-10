import type { Page } from "@playwright/test";

/** DB の読み取りの失敗のときの backend の文（`read_failures.py` の 500 `database_read_failed`。#4222）。 */
export const DATABASE_READ_FAILED_MESSAGE = "データを読み取れませんでした。データは失われていません。";

/**
 * `pathname` の GET を、`recover()` を呼ぶまで DB の読み取りの失敗（500 `database_read_failed`）にする。
 * 後から登録した route が先に呼ばれる（mock-api の成功の応答を上書きし、`recover()` の後は mock-api に戻す）。
 */
export async function failDatabaseReads(page: Page, pathname: string) {
  let failing = true;
  await page.route(
    (url) => url.pathname === pathname,
    (route) =>
      failing && route.request().method() === "GET"
        ? route.fulfill({
            status: 500,
            json: {
              data: null,
              error_messages: [DATABASE_READ_FAILED_MESSAGE],
              warning_messages: [],
              error_code: "database_read_failed",
              error_details: {},
            },
          })
        : route.fallback(),
  );
  return {
    recover() {
      failing = false;
    },
  };
}
