import { expect, type Page } from "@playwright/test";

import { measureHorizontalOverflow } from "../../../../../platform/packages/ui/e2e/layout-checks";

/**
 * ページ全体（documentElement と main）が横にあふれない（1px は丸めの許容）。spec ごとに写していた検査をまとめた（#4806）。
 * 画面幅を変えた直後などの一過性のあふれは poll で待つ。表などの意図的な横スクロールは各自の箱の中に閉じる前提。
 */
export async function expectNoPageHorizontalOverflow(page: Page, label = "ページ全体（documentElement / main）の横あふれ") {
  await expect.poll(() => measureHorizontalOverflow(page), { message: label, timeout: 2000 }).toBeLessThanOrEqual(1);
}
