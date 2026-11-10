import type { Page } from "@playwright/test";

import { findFieldLoadFailureViolations } from "../../../platform/packages/ui/e2e/field-load-failure";
import { expect, test } from "./fixtures/mock-api";
import { expectNoPageHorizontalOverflow } from "./fixtures/page-overflow";

// #4817: 欄の候補の読み取りの失敗は、共有の部品（`DataLoadFailureState` の `layout="field"`）で、欄ごとの文の
// 1 行と再試行を欄の直下に出す（候補なし・欄ごと消すのと取り違えない）。合成の mock で API を失敗させる。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

/** 自動の再試行の間隔が短い「その他」の失敗。 */
function readFailure(message: string) {
  return {
    status: 500,
    json: { data: null, error_messages: [message], warning_messages: [], error_code: "database_read_failed" },
  };
}

/** `GET <path>` を、`state.fail` の間だけ失敗させる（後から登録した route が fixture の応答より先に呼ばれる）。 */
async function failWhile(page: Page, path: string, message: string) {
  const state = { fail: true };
  await page.route(
    (url) => url.pathname === path,
    (route) => (state.fail && route.request().method() === "GET" ? route.fulfill(readFailure(message)) : route.fallback())
  );
  return state;
}

for (const viewport of VIEWPORTS) {
  test(`自動実行の業務 Agent の候補を読み取れないときは、欄の直下に失敗と再試行を出す (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const agents = await failWhile(page, "/api/agents", "業務 Agent の一覧を読み取れませんでした。");
    await page.goto("/automations?id=new");

    const failure = page.getByTestId("automation-agent-load-failed");
    await expect(failure.getByRole("alert")).toHaveText("業務 Agent の候補を読み込めませんでした。", { timeout: 15_000 });
    // 「使える業務 Agent がありません」と言わない。backend の文は畳んだ「詳細」に入れる。
    await expect(page.getByText("実行できる業務 Agent がありません", { exact: false })).toHaveCount(0);
    await expect(failure.getByText("業務 Agent の一覧を読み取れませんでした。")).toBeHidden();
    expect(await findFieldLoadFailureViolations(page, { expectCount: 1 })).toEqual([]);
    const field = page.locator("#automation-agent");
    expect((await failure.boundingBox())!.y).toBeGreaterThan((await field.boundingBox())!.y);
    await expectNoPageHorizontalOverflow(page);
    await failure.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`automation-agent-load-failed-${viewport.name}.png`) });

    agents.fail = false;
    await failure.getByRole("button", { name: "再試行" }).focus();
    await page.keyboard.press("Enter");
    await expect(failure).toHaveCount(0);
  });

  test(`監査のツール名の候補を読み取れないときは、条件の欄の直下に失敗と再試行を出す (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const tools = await failWhile(page, "/api/tools", "ツールの一覧を読み取れませんでした。");
    await page.goto("/audit");

    const failure = page.getByTestId("audit-tool-name-load-failed");
    await expect(failure.getByRole("alert")).toHaveText("ツール名の候補を読み込めませんでした。", { timeout: 15_000 });
    expect(await findFieldLoadFailureViolations(page, { expectCount: 1 })).toEqual([]);
    // 条件の欄はそのまま出す（記録に出たツール名は候補に残る）。
    await expect(page.locator("#audit-tool-name")).toBeVisible();
    await expectNoPageHorizontalOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`audit-tool-name-load-failed-${viewport.name}.png`) });

    tools.fail = false;
    await failure.getByRole("button", { name: "再試行" }).click();
    await expect(failure).toHaveCount(0);
  });

  test(`API キーの「使える業務 Agent」の候補を読み取れないときは、欄を消さずに失敗と再試行を出す (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const agents = await failWhile(page, "/api/agents", "業務 Agent の一覧を読み取れませんでした。");
    await page.goto("/account?tab=api-keys");

    const failure = page.getByTestId("api-key-agent-scope-load-failed");
    await expect(failure.getByRole("alert")).toHaveText("使える業務 Agent の候補を読み込めませんでした。", {
      timeout: 15_000,
    });
    // 候補を渡さない製品向けの案内（「絞るのは Agent の画面」）と取り違えない。
    await expect(page.getByTestId("api-key-agent-scope-elsewhere")).toHaveCount(0);
    expect(await findFieldLoadFailureViolations(page, { expectCount: 1 })).toEqual([]);
    await expectNoPageHorizontalOverflow(page);
    await failure.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`api-key-agent-scope-load-failed-${viewport.name}.png`) });

    agents.fail = false;
    await failure.getByRole("button", { name: "再試行" }).click();
    await expect(failure).toHaveCount(0);
    await expect(page.getByTestId("api-key-agent-scope-all")).toBeVisible();
  });

  test(`評価セットの業務 Agent の候補を読み取れないときは、欄の直下に失敗と再試行を出す (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const agents = await failWhile(page, "/api/agents", "業務 Agent の一覧を読み取れませんでした。");
    await page.goto("/evaluation?id=new");

    const failure = page.getByTestId("evaluation-set-agent-load-failed");
    await expect(failure.getByRole("alert")).toHaveText("業務 Agent の候補を読み込めませんでした。", { timeout: 15_000 });
    expect(await findFieldLoadFailureViolations(page, { expectCount: 1 })).toEqual([]);
    const field = page.locator("#evaluation-set-agent");
    expect((await failure.boundingBox())!.y).toBeGreaterThan((await field.boundingBox())!.y);
    await expectNoPageHorizontalOverflow(page);
    await failure.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath(`evaluation-set-agent-load-failed-${viewport.name}.png`) });

    agents.fail = false;
    await failure.getByRole("button", { name: "再試行" }).click();
    await expect(failure).toHaveCount(0);
  });
}
