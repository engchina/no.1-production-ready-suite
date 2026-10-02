import type { Page } from "@playwright/test";

import { MOCK_NOW, expect, test } from "./fixtures/mock-api";
import { dbUser } from "./fixtures/auth";

// #778: 業務 Agent を MCP で呼ぶ外部のクライアント向けの API キー。

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

for (const viewport of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    test(`API キーを作ると秘密を 1 回だけ出し、一覧から削除できる (${viewport.name}, ${theme})`, async ({ page, mockApi }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await useTheme(page, theme);
      await page.goto("/settings/api-keys");

      await expect(page.getByRole("heading", { name: "API キー", level: 1 })).toBeVisible();
      await expect(page.getByTestId("api-keys-endpoint")).toHaveText(/\/api\/mcp$/);
      await expect(page.getByText("API キーはまだありません")).toBeVisible();

      // 名前と業務 Agent は必須（選んだ業務 Agent だけが既定）。
      await page.getByTestId("api-key-create").click();
      await expect(page.getByText("名前を入力してください。")).toBeVisible();
      await expect(page.getByText("業務 Agent を 1 つ以上選んでください。")).toBeVisible();
      expect(mockApi.lastRequest("POST", "/api/settings/api-keys")).toBeUndefined();

      await page.getByLabel("名前").fill("基幹システムの問い合わせ連携");
      await page.locator("#api-key-agents").click();
      await page.getByRole("option", { name: /汎用業務 Agent/ }).click();
      await page.keyboard.press("Escape");
      await page.getByTestId("api-key-create").click();

      const created = page.getByTestId("api-key-created");
      await expect(created).toContainText("この秘密は今だけ表示します");
      await expect(page.getByTestId("api-key-token")).toHaveText(/^prak_[0-9a-f]{16}_/);
      expect(mockApi.lastRequest("POST", "/api/settings/api-keys")?.body).toEqual({
        name: "基幹システムの問い合わせ連携",
        agent_ids: ["default"],
        expires_in_days: 90,
        run_as_user_uuid: null,
      });
      const table = page.getByRole("table", { name: "API キーの一覧" });
      const row = table.getByRole("row", { name: /基幹システムの問い合わせ連携/ });
      await expect(row).toContainText("汎用業務 Agent");
      await expect(row).toContainText("未使用");
      await expectNoHorizontalOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`api-keys-${viewport.name}-${theme}.png`), fullPage: true });

      // 保管したら秘密を消す（再表示しない）。
      await created.getByRole("button", { name: "保管しました" }).click();
      await expect(page.getByTestId("api-key-token")).toHaveCount(0);

      await page.getByTestId(`api-key-row-actions-${String(mockApi.state.apiKeys[0].id)}`).click();
      await page.getByRole("menuitem", { name: "削除" }).click();
      const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
      await expect(dialog.getByText("「基幹システムの問い合わせ連携」を削除しますか?")).toBeVisible();
      await dialog.getByRole("button", { name: "削除", exact: true }).click();
      await expect(page.getByText("API キーを削除しました")).toBeVisible();
      await expect(page.getByText("API キーはまだありません")).toBeVisible();
    });
  }
}

test("すべての業務 Agent・無期限のキーを作れ、保存先が無いときは知らせる", async ({ page, mockApi }) => {
  mockApi.state.apiKeysPersistent = false;
  await page.goto("/settings/api-keys");
  await expect(page.getByText("作成したキーはバックエンドの再起動で消えます", { exact: false })).toBeVisible();

  await page.getByLabel("名前").fill("社内ポータル");
  await page.getByRole("radio", { name: "使えるすべての業務 Agent" }).check();
  await expect(page.locator("#api-key-agents")).toHaveCount(0);
  await page.locator("#api-key-expiry").click();
  await page.getByRole("option", { name: "無期限" }).click();
  await page.getByTestId("api-key-create").click();
  await expect(page.getByTestId("api-key-token")).toBeVisible();
  expect(mockApi.lastRequest("POST", "/api/settings/api-keys")?.body).toEqual({
    name: "社内ポータル",
    agent_ids: null,
    expires_in_days: null,
    run_as_user_uuid: null,
  });
  const row = page.getByRole("table", { name: "API キーの一覧" }).getByRole("row", { name: /社内ポータル/ });
  await expect(row).toContainText("すべて");
  await expect(row).toContainText("無期限");
});

test("Agent 管理の権限が無い利用者は一覧だけを見る", async ({ page, mockApi }) => {
  mockApi.state.apiKeys.push({
    id: "00000000000000aa",
    name: "既存のキー",
    owner_user_uuid: "u-1",
    owner_display_name: "山田 太郎",
    created_by_user_uuid: "u-admin",
    created_by_display_name: "管理 太郎",
    agent_ids: ["default"],
    token_prefix: "prak_00000000000000aa_abcd",
    created_at: MOCK_NOW,
    expires_at: MOCK_NOW,
    last_used_at: MOCK_NOW,
    expired: true,
  });
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.settings_api_keys"] }));
  await page.goto("/settings/api-keys");
  const row = page.getByRole("table", { name: "API キーの一覧" }).getByRole("row", { name: /既存のキー/ });
  await expect(row).toContainText("期限切れ");
  await expect(row).toContainText("山田 太郎");
  await expect(page.getByRole("heading", { name: "API キーの作成" })).toHaveCount(0);
  await expect(page.getByTestId("api-key-row-actions-00000000000000aa")).toHaveCount(0);
});

test("システム管理者は、キーを連携用の専用の利用者として動かせる", async ({ page, mockApi }) => {
  await page.goto("/settings/api-keys");
  await page.getByLabel("名前").fill("基幹システム");
  await page.getByRole("radio", { name: "使えるすべての業務 Agent" }).check();
  await page.locator("#api-key-run-as").click();
  await page.getByRole("option", { name: /実行 花子/ }).click();
  await page.getByTestId("api-key-create").click();
  await expect(page.getByTestId("api-key-token")).toBeVisible();
  expect(
    (mockApi.lastRequest("POST", "/api/settings/api-keys")?.body as { run_as_user_uuid: string }).run_as_user_uuid
  ).toBe("u-operator");
  const row = page.getByRole("table", { name: "API キーの一覧" }).getByRole("row", { name: /基幹システム/ });
  await expect(row).toContainText("実行 花子");
  await expect(row).toContainText("ローカル利用者");
});

test("システム管理者でなければ、実行する利用者は選べない（自分として動く）", async ({ page, mockApi }) => {
  mockApi.setCurrentUser(dbUser({ permissions: ["menu.settings_api_keys", "agent.admin"] }));
  await page.goto("/settings/api-keys");
  await expect(page.getByRole("heading", { name: "API キーの作成" })).toBeVisible();
  await expect(page.locator("#api-key-run-as")).toHaveCount(0);
});
