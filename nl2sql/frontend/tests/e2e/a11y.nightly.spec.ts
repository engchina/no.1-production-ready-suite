import AxeBuilder from "@axe-core/playwright";

import { expect, test, type Page, type TestInfo } from "./_helpers/test";
import { mockChatRequirement } from "./_helpers/chat-requirement";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

/**
 * 主要な画面の axe（a11y）の確認（#1595）。nightly（e2e-nightly.yml）だけで実行し、PR の e2e では実行しない
 * （名前の `*.nightly.spec.ts`。platform/scripts/e2e-impact/nightly-only.ts）。
 * ローカルは `npx playwright test tests/e2e/a11y.nightly.spec.ts`。
 *
 * - 対象は WCAG 2.x の A / AA（2.0 と 2.1）。失敗にするのは impact が serious / critical の違反だけ。
 * - 違反は rule id・impact・件数・対象（selector）を失敗の文と添付（axe-violations.json）に出す。
 * - 既知の例外は KNOWN_EXCEPTIONS に rule id・画面・理由を書く（直すのに設計の判断が要るもの。別 Issue で直す）。
 */

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING_IMPACTS = new Set(["serious", "critical"]);

/** 既知の例外（画面の path → 除く rule id）。空なら除かない。 */
const KNOWN_EXCEPTIONS: Record<string, readonly string[]> = {};

const PROFILE = {
  id: "sales",
  name: "売上分析",
  description: "売上の集計",
  archived: false,
  allowed_tables: ["APP.SALES"],
  allowed_views: [],
  allowed_table_count: 1,
  allowed_view_count: 0,
  version: 1,
};

const HISTORY = [
  {
    id: "history-1",
    question: "今月の売上を確認",
    engine: "select_ai",
    generated_sql: "SELECT SUM(AMOUNT) FROM APP.SALES",
    executable_sql: "SELECT SUM(AMOUNT) FROM APP.SALES",
    created_at: "2026-06-22T10:00:00.000Z",
    elapsed_ms: 250,
    feedback_rating: "good",
    profile_id: "sales",
    profile_name: "売上分析",
    rewritten_question: "今月の売上",
    safety_is_safe: true,
    result_row_count: 1,
    result_columns: ["SUM(AMOUNT)"],
    feedback_comment: "",
  },
  {
    id: "history-2",
    question: "顧客を削除",
    engine: "select_ai",
    generated_sql: "DELETE FROM APP.CUSTOMERS",
    executable_sql: "",
    created_at: "2026-06-21T10:00:00.000Z",
    elapsed_ms: 120,
    feedback_rating: "",
    profile_id: "sales",
    profile_name: "売上分析",
    rewritten_question: "",
    safety_is_safe: false,
    result_row_count: 0,
    result_columns: [],
    feedback_comment: "",
  },
];

/** 主要な画面の取得を数件ずつ返す（読み込んだ後の画面を確かめる）。 */
async function mockMainScreens(page: Page) {
  await mockDatabaseGateReady(page);
  await mockChatRequirement(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill({ json: { data: { items: [PROFILE], total: 1, next_cursor: null } } }),
  );
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) => route.fulfill({ json: { data: PROFILE } }));
  await page.route("**/api/nl2sql/chats**", (route) =>
    route.fulfill({ json: { data: { items: [], next_cursor: null, total: 0, limit: 10 } } }),
  );
  await page.route("**/api/nl2sql/history**", (route) =>
    route.fulfill({ json: { data: { items: HISTORY, next_cursor: "", total: HISTORY.length } } }),
  );
}

const SCREENS = [
  { name: "チャット", path: "/chat", heading: "チャット" },
  { name: "実行履歴", path: "/history", heading: "実行履歴" },
  { name: "システム設定（外観と証明書）", path: "/settings/appearance", heading: "外観と証明書" },
];

/** 表示の途中（フェードイン・色の transition）の色で color-contrast を測らない。無限の animation（Spinner など）は待たない。 */
async function waitForSettledAnimations(page: Page) {
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every((animation) => animation.playState !== "running" || animation.effect?.getComputedTiming().iterations === Infinity),
  );
}

async function expectNoSeriousViolations(page: Page, testInfo: TestInfo, path: string) {
  const exceptions = KNOWN_EXCEPTIONS[path] ?? [];
  await waitForSettledAnimations(page);
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).disableRules([...exceptions]).analyze();
  const blocking = results.violations.filter((violation) => BLOCKING_IMPACTS.has(violation.impact ?? ""));
  const summary = blocking.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    count: violation.nodes.length,
    help: violation.help,
    // 対象の selector と、axe の理由の 1 行目（color-contrast なら実測の比と色）。
    targets: violation.nodes
      .slice(0, 5)
      .map((node) => `${node.target.join(" ")}（${node.failureSummary?.split("\n").at(1)?.trim() ?? ""}）`),
  }));
  if (summary.length) {
    await testInfo.attach("axe-violations.json", { body: JSON.stringify(summary, null, 2), contentType: "application/json" });
  }
  const message = summary
    .map((item) => `${item.rule}（${item.impact}・${item.count} 件）: ${item.help}\n    ${item.targets.join("\n    ")}`)
    .join("\n");
  expect(summary, `${path} の serious / critical の a11y 違反:\n${message}`).toEqual([]);
}

test.describe("主要な画面の a11y（axe）", { tag: "@a11y" }, () => {
  test.beforeEach(async ({ page }) => {
    await mockMainScreens(page);
  });

  for (const screen of SCREENS) {
    test(`${screen.name}（${screen.path}）に WCAG 2.1 A / AA の serious / critical の違反が無い`, async ({ page }, testInfo) => {
      await page.goto(screen.path);
      await expect(page.getByRole("heading", { level: 1, name: screen.heading })).toBeVisible();
      // 読み込み中の Skeleton ではなく、読み込んだ後の画面を確かめる。
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      await expectNoSeriousViolations(page, testInfo, screen.path);
    });
  }
});
