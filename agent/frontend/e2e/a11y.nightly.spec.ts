import AxeBuilder from "@axe-core/playwright";
import type { Page, TestInfo } from "@playwright/test";

import { expect, test } from "./fixtures/mock-api";

/**
 * 主要な画面の axe（a11y）の確認（#1595）。nightly（e2e-nightly.yml）だけで実行し、PR の e2e では実行しない
 * （名前の `*.nightly.spec.ts`。platform/scripts/e2e-impact/nightly-only.ts）。ローカルは `npx playwright test e2e/a11y.nightly.spec.ts`。
 *
 * - 対象は WCAG 2.x の A / AA（2.0 と 2.1）。失敗にするのは impact が serious / critical の違反だけ。
 * - 違反は rule id・impact・件数・対象（selector）を失敗の文と添付（axe-violations.json）に出す。
 * - 既知の例外は KNOWN_EXCEPTIONS に rule id・画面・理由を書く（直すのに設計の判断が要るもの。別 Issue で直す）。
 */

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];
const BLOCKING_IMPACTS = new Set(["serious", "critical"]);

/** 既知の例外（画面の path → 除く rule id）。空なら除かない。 */
const KNOWN_EXCEPTIONS: Record<string, readonly string[]> = {};

const SCREENS = [
  { name: "チャット", path: "/chat" },
  { name: "業務 Agent", path: "/agents" },
  { name: "実行", path: "/runs" },
  { name: "承認", path: "/approvals" },
  { name: "システム設定（モデル）", path: "/settings/model" },
];

async function expectNoSeriousViolations(page: Page, testInfo: TestInfo, path: string) {
  const exceptions = KNOWN_EXCEPTIONS[path] ?? [];
  const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).disableRules([...exceptions]).analyze();
  const blocking = results.violations.filter((violation) => BLOCKING_IMPACTS.has(violation.impact ?? ""));
  const summary = blocking.map((violation) => ({
    rule: violation.id,
    impact: violation.impact,
    count: violation.nodes.length,
    help: violation.help,
    targets: violation.nodes.slice(0, 5).map((node) => node.target.join(" ")),
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
  for (const screen of SCREENS) {
    test(`${screen.name}（${screen.path}）に WCAG 2.1 A / AA の serious / critical の違反が無い`, async ({ page }, testInfo) => {
      await page.goto(screen.path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      // 読み込み中の Skeleton ではなく、読み込んだ後の画面を確かめる。
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
      await expectNoSeriousViolations(page, testInfo, screen.path);
    });
  }
});
