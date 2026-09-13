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
 * `/settings/database` の SystemTablesCard 用 status stub。
 *
 * `/api/settings/database/system-tables` は `**\/api/settings/database**` に一致するため、
 * 設定ページの mock が catch-all で別 payload を返すとカードがエラー表示になる。
 * 同じページの他カード(ADB 管理・Wallet)を検証する spec は、この stub を明示 route する。
 */
export const SYSTEM_TABLES_STATUS_OK = {
  data: {
    status: "ready",
    schema_version: "0003",
    schema_head: "0003",
    applied_versions: ["0001", "0002", "0003"],
    pending_versions: [],
    expected_object_count: 96,
    existing_object_count: 96,
    expected_table_count: 24,
    existing_table_count: 24,
    missing_objects: [],
    retired_objects: [],
    tables: [],
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: 3,
      updated_at: "2026-07-23T00:00:00+09:00",
    },
  },
  error_messages: [],
  warning_messages: [],
};

/**
 * ページ全体が横スクロールせず、document に第2の縦スクロールがないことを検証する。
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
      return {
        horizontal: Math.max(
          root.scrollWidth - root.clientWidth,
          main ? main.scrollWidth - main.clientWidth : 0
        ),
        documentVertical: root.scrollHeight - root.clientHeight,
      };
    });
  // 1px はスクロールバー/小数丸めの許容。遷移沈静まで最大 2s リトライ。
  await expect
    .poll(async () => (await measure()).horizontal, {
      message: "ページ全体(documentElement / main)の横はみ出し",
      timeout: 2000,
    })
    .toBeLessThanOrEqual(1);
  await expect
    .poll(async () => (await measure()).documentVertical, {
      message: "documentElement に第2の縦スクロールがないこと",
      timeout: 2000,
    })
    .toBeLessThanOrEqual(1);
}

/** main を末尾までスクロールしたとき、実コンテンツの後ろに空白が残らないことを検証する。 */
export async function expectMainScrollEndsAtContent(page: Page): Promise<void> {
  const main = page.getByRole("main");
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

/**
 * 一覧の各行で、セルの内容（要素と文字）が列の境界を超えないことを実測する。
 *
 * - 次のセルが同じ行に並ぶ場合は「内容の右端 <= 次のセルの左端」、最後のセルと縦に積まれる場合は「<= 自セルの右端」。
 * - 内容の左端も自セルの左端を下回らないこと（右寄せの nowrap が左の列へはみ出す場合）。
 * - `overflow: hidden` の祖先（truncate / line-clamp）で切り取られる部分は見えないので数えない。
 * 違反を「列見出し: 内容 はみ出し量」の配列で返す（空配列が合格）。
 */
export async function measureTableCellOverflow(page: Page, tableSelector: string): Promise<string[]> {
  return page.locator(tableSelector).evaluateAll((tables) => {
    const violations: string[] = [];
    for (const table of tables) {
      const headers = Array.from(table.querySelectorAll("thead th, [role='columnheader']")).map(
        (th) => th.textContent?.trim() ?? ""
      );
      for (const row of Array.from(table.querySelectorAll("tr, [role='row']"))) {
        const cells = Array.from(row.querySelectorAll("td, th, [role='cell'], [role='columnheader']")).filter(
          (cell) => cell.parentElement?.closest("tr, [role='row']") === row && cell.getBoundingClientRect().width > 0
        );
        cells.forEach((cell, index) => {
          const cellRect = cell.getBoundingClientRect();
          const nextRect = cells[index + 1]?.getBoundingClientRect();
          const limitRight =
            nextRect && nextRect.top < cellRect.bottom - 1 && nextRect.left >= cellRect.left ? nextRect.left : cellRect.right;
          const walker = document.createTreeWalker(cell, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
          for (let node = walker.nextNode(); node; node = walker.nextNode()) {
            const element = node.nodeType === Node.TEXT_NODE ? node.parentElement : (node as Element);
            if (!element) continue;
            if (node.nodeType === Node.TEXT_NODE && !node.textContent?.trim()) continue;
            let rect: DOMRect;
            if (node.nodeType === Node.TEXT_NODE) {
              const range = document.createRange();
              range.selectNodeContents(node);
              rect = range.getBoundingClientRect();
            } else {
              rect = element.getBoundingClientRect();
            }
            if (rect.width <= 1 || rect.height <= 1) continue;
            let right = rect.right;
            let left = rect.left;
            for (let ancestor = element; ancestor && ancestor !== row; ancestor = ancestor.parentElement!) {
              if (ancestor === element && node.nodeType !== Node.TEXT_NODE) continue;
              if (getComputedStyle(ancestor).overflowX !== "visible") {
                const clip = ancestor.getBoundingClientRect();
                right = Math.min(right, clip.right);
                left = Math.max(left, clip.left);
              }
            }
            const label = `${headers[index] ?? index}: ${(node.textContent ?? "").trim().slice(0, 24)}`;
            if (right > limitRight + 0.5) violations.push(`${label} right +${(right - limitRight).toFixed(1)}px`);
            if (left < cellRect.left - 0.5) violations.push(`${label} left -${(cellRect.left - left).toFixed(1)}px`);
          }
        });
      }
    }
    return Array.from(new Set(violations));
  });
}
