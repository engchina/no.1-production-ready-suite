import type { Page } from "@playwright/test";

import { dbUser } from "./fixtures/auth";
import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";
import { chooseSelectFieldOption } from "./fixtures/select-field";

// #784: 業務 Agent の自動実行（スケジュール・Webhook）。

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile-375", width: 375, height: 812 },
];

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(overflow).toBeLessThanOrEqual(0);
}

async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-agent.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

function seedAutomation(mockApi: MockApi, overrides: Record<string, unknown> = {}) {
  mockApi.state.automations.push({
    id: "auto-seeded",
    agent_id: "default",
    name: "毎朝の売上の要約",
    goal: "昨日の売上を要約してください。",
    enabled: true,
    trigger: "schedule",
    schedule: { frequency: "daily", time: "09:00", weekdays: [0], minute: 0, timezone: "Asia/Tokyo" },
    run_as_user_uuid: "local",
    created_by_user_uuid: "local",
    webhook_token_prefix: null,
    next_run_at: MOCK_NOW,
    last_run_at: null,
    last_run_id: null,
    last_trigger: null,
    last_result: null,
    last_message: null,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
    ...overrides,
  });
}

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`スケジュールの自動実行を作り、一覧に次回とトリガーを出す (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/automations");
      await expect(page.getByRole("heading", { name: "自動実行", level: 1 })).toBeVisible();
      await expect(page.getByText("自動実行はまだありません")).toBeVisible();

      await page.getByRole("button", { name: "自動実行を作成" }).first().click();
      await expect(page).toHaveURL(/\/automations\?id=new$/);
      // 必須の入力が無ければ保存しない。
      await page.getByRole("button", { name: "作成", exact: true }).first().click();
      await expect(page.getByText("名前を入力してください。")).toBeVisible();
      await expect(page.getByText("指示を入力してください。")).toBeVisible();

      await page.getByLabel("名前").fill("週次の品質の点検");
      await page.locator("#automation-goal").fill("先週の不良率を確認し、基準を超えたラインを報告してください。");
      await chooseSelectFieldOption(page.locator("#automation-frequency"), "weekly");
      await page.getByRole("checkbox", { name: "水" }).check();
      await page.getByLabel("時刻").fill("08:30");
      await page.screenshot({ path: testInfo.outputPath(`automation-editor-${viewport.name}-${theme}.png`), fullPage: true });
      await page.getByRole("button", { name: "作成", exact: true }).first().click();

      await expect(page.getByText("自動実行を作成しました")).toBeVisible();
      await expect(page).toHaveURL(/\/automations\?id=auto-1$/);
      const body = mockApi.lastRequest("POST", "/api/automations")?.body as Record<string, unknown>;
      expect(body).toMatchObject({
        agent_id: "default",
        name: "週次の品質の点検",
        enabled: true,
        trigger: "schedule",
        schedule: { frequency: "weekly", time: "08:30", weekdays: [0, 2], minute: 0 },
      });
      await expect(page.getByTestId("automation-next")).toContainText("次回の実行");

      await page.getByRole("button", { name: "一覧へ戻る" }).last().click();
      const row = page.getByRole("table", { name: "自動実行の一覧" }).getByRole("row", { name: /週次の品質の点検/ });
      await expect(row).toContainText("毎週 月・水 08:30");
      await expect(row).toContainText("有効");
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`automations-${viewport.name}-${theme}.png`), fullPage: true });
    });
  }
}

test("Webhook の自動実行は保存後に URL を出し、秘密を 1 回だけ表示し、発行し直す前に確かめる", async ({ page, mockApi }) => {
  await page.goto("/automations?id=new");
  await page.getByLabel("名前").fill("受注の通知");
  await page.locator("#automation-goal").fill("受け取った受注を確認し、在庫の不足があれば知らせてください。");
  await page.getByRole("radio", { name: "Webhook" }).check();
  await expect(page.getByText("保存すると Webhook の URL が決まります。")).toBeVisible();
  await page.getByRole("button", { name: "作成", exact: true }).first().click();
  await expect(page.getByText("自動実行を作成しました")).toBeVisible();
  expect((mockApi.lastRequest("POST", "/api/automations")?.body as { schedule: unknown }).schedule).toBeNull();

  await expect(page.getByTestId("automation-webhook-url")).toHaveText(/\/api\/hooks\/auto-1$/);
  await expect(page.getByText("秘密はまだ発行していません。")).toBeVisible();
  await page.getByTestId("automation-issue-token").click();
  await expect(page.getByTestId("automation-webhook-token")).toContainText("prwh_");
  await page.getByRole("button", { name: "保管しました" }).click();
  await expect(page.getByTestId("automation-webhook-token")).toHaveCount(0);

  await page.getByTestId("automation-issue-token").click();
  const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(dialog.getByText("秘密を発行し直しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page.getByTestId("automation-webhook-token")).toHaveCount(0);
});

test("一覧から今すぐ実行し、実行履歴に出す。未保存の離脱を確かめ、削除できる", async ({ page, mockApi }) => {
  seedAutomation(mockApi);
  await page.goto("/automations");
  await page.getByTestId("automation-row-actions-auto-seeded").click();
  await page.getByRole("menuitem", { name: "今すぐ実行" }).click();
  await expect(page.getByText("Run を作りました")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/automations/auto-seeded/run")).toBeDefined();

  await page.getByRole("table", { name: "自動実行の一覧" }).getByRole("link", { name: /毎朝の売上の要約/ }).click();
  await expect(page.getByTestId("automation-last")).toContainText("Run を作りました。");
  const history = page.getByRole("table", { name: "実行履歴" });
  await expect(history).toContainText("今すぐ実行");
  await expect(history).toContainText("run-auto-1");

  await page.getByLabel("名前").fill("毎朝の売上の要約（改）");
  await page.getByRole("button", { name: "一覧へ戻る" }).last().click();
  const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(dialog.getByText("変更を破棄しますか")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expect(page.getByLabel("名前")).toHaveValue("毎朝の売上の要約");

  const actions = page.getByTestId("automation-actions");
  await expect(actions).toBeVisible();
  const deleteButton = actions.getByRole("button", { name: "削除" });
  if (await deleteButton.count()) {
    await deleteButton.click();
  } else {
    await page.getByTestId("automation-actions-more").click();
    await page.getByRole("menuitem", { name: "削除" }).click();
  }
  await expect(dialog.getByText("「毎朝の売上の要約」を削除しますか?")).toBeVisible();
  await dialog.getByRole("button", { name: "削除", exact: true }).click();
  await expect(page.getByText("自動実行を削除しました")).toBeVisible();
  await expect(page).toHaveURL(/\/automations$/);
});

test("Agent 管理の権限が無い利用者は閲覧だけ", async ({ page, mockApi }) => {
  seedAutomation(mockApi, { last_result: "skipped", last_run_at: MOCK_NOW, last_message: "前回の Run が終わっていないため、この回は実行しませんでした。" });
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.automations"] }));
  await page.goto("/automations");
  await expect(page.getByRole("button", { name: "自動実行を作成" })).toHaveCount(0);
  const row = page.getByRole("table", { name: "自動実行の一覧" }).getByRole("row", { name: /毎朝の売上の要約/ });
  await expect(row).toContainText("飛ばした");
  await expect(page.getByTestId("automation-row-actions-auto-seeded")).toHaveCount(0);

  await row.getByRole("link", { name: /毎朝の売上の要約/ }).click();
  await expect(page.getByLabel("名前")).toBeDisabled();
  await expect(page.getByRole("button", { name: "保存", exact: true })).toHaveCount(0);
});

test("保存先が無いときは知らせ、読み込み中は経過時間を出す", async ({ page, mockApi }) => {
  mockApi.state.automationsPersistent = false;
  await page.route("**/api/automations", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.fallback();
  });
  await page.goto("/automations");
  await expect(page.getByTestId("automations-loading")).toContainText("自動実行を読み込んでいます");
  await expect(page.getByText("作成した自動実行はバックエンドの再起動で消えます", { exact: false })).toBeVisible();
});
