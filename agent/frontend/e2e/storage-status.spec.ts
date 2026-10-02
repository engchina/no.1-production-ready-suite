import type { Page } from "@playwright/test";

import { dbUser } from "./fixtures/auth";
import { RUNTIME_STORAGE_MEMORY, expect, test } from "./fixtures/mock-api";

// 保存先（#839）。保存先がメモリのときは、作成・変更した内容が再起動で消えることを、定義・実行を作る画面と
// 運用設定の画面に出し、直し方は実行環境の「保存先」に 1 か所だけ書く。DB が設定済みなら DB の設定へは案内しない。

const NOTICE_TITLE = "作成・変更した内容は、バックエンドの再起動で消えます";
const SETTING = "AGENT_RUNTIME_REPOSITORY_BACKEND=oracle_checkpoint";

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

test("保存しているときは、実行環境に「保存しています」を出し、他の画面に案内を出さない", async ({ page }) => {
  await page.goto("/runtimes");
  const card = page.getByTestId("runtime-storage-card");
  await expect(card).toContainText("保存しています");
  await expect(page.getByTestId("runtime-storage-backend")).toHaveText("Oracle AI Database");
  await expect(page.getByTestId("runtime-storage-fix")).toHaveCount(0);

  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "業務 Agent", level: 1 })).toBeVisible();
  await expect(page.getByTestId("storage-not-persistent-notice")).toHaveCount(0);
});

test("保存先がメモリなら、各画面に案内を出し、実行環境で直し方（設定する値）を示す", async ({ page, mockApi }) => {
  mockApi.state.runtimeStorage = { ...RUNTIME_STORAGE_MEMORY };

  for (const path of [
    "/agents",
    "/runs",
    "/skills",
    "/automations",
    "/evaluation",
    "/settings/mcp-connections",
    "/settings/api-keys",
    "/settings/system-tables",
  ]) {
    await page.goto(path);
    const notice = page.getByTestId("storage-not-persistent-notice");
    await expect(notice, path).toContainText(NOTICE_TITLE);
    await expect(notice, path).toContainText("データベースは設定済みですが、保存先がメモリのため");
    await expect(notice, path).not.toContainText("データベースを設定してください");
  }
  await expectNoPageOverflow(page);

  await page.goto("/agents");
  await page.getByTestId("storage-notice-open-runtime").click();
  await expect(page).toHaveURL(/\/runtimes$/);
  const card = page.getByTestId("runtime-storage-card");
  await expect(card).toContainText("再起動で消えます");
  await expect(page.getByTestId("runtime-storage-backend")).toHaveText("メモリ（保存しない）");
  await expect(card).toContainText("設定済み");
  const fix = page.getByTestId("runtime-storage-fix");
  await expect(fix).toContainText("agent/backend/.env");
  await expect(fix).toContainText(SETTING);
  // DB は設定済みなので、データベースの設定へは案内しない。
  await expect(page.getByTestId("runtime-storage-open-database-settings")).toHaveCount(0);
  // 実行環境の画面には、他の画面の案内（実行環境へのリンク）を重ねない。
  await expect(page.getByTestId("storage-not-persistent-notice")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("DB も未設定なら、データベースの設定へ案内する", async ({ page, mockApi }) => {
  mockApi.state.runtimeStorage = {
    ...RUNTIME_STORAGE_MEMORY,
    database_configured: false,
    reason: "database_not_configured",
  };
  await page.goto("/settings/api-keys");
  await expect(page.getByTestId("storage-not-persistent-notice")).toContainText("データベースが未設定のため");

  await page.goto("/runtimes");
  await expect(page.getByTestId("runtime-storage-card")).toContainText("未設定");
  await expect(page.getByTestId("runtime-storage-fix")).toContainText("システム設定 > データベースを設定し");
  await page.getByTestId("runtime-storage-open-database-settings").click();
  await expect(page).toHaveURL(/\/settings\/database/);
});

test("バックアップと復元には、書き出すと復元できる範囲を添える", async ({ page, mockApi }) => {
  mockApi.state.runtimeStorage = { ...RUNTIME_STORAGE_MEMORY };
  await page.goto("/settings/runtime-snapshot");
  const notice = page.getByTestId("storage-not-persistent-notice");
  await expect(notice).toContainText(NOTICE_TITLE);
  await expect(notice).toContainText("業務 Agent と実行の履歴は再起動の後に復元できます");
  await expectNoPageOverflow(page);
});

test("実行環境を開けない利用者には、管理者への依頼を出す（リンクは出さない）", async ({ page, mockApi }) => {
  mockApi.state.runtimeStorage = { ...RUNTIME_STORAGE_MEMORY };
  mockApi.setCurrentUser(
    dbUser({ login_user_id: "viewer.user", permissions: ["agent.runs.view"], allowed_agent_ids: ["default"] })
  );
  await page.goto("/runs");
  const notice = page.getByTestId("storage-not-persistent-notice");
  await expect(notice).toContainText(NOTICE_TITLE);
  await expect(notice).toContainText("システム管理者に依頼してください");
  await expect(page.getByTestId("storage-notice-open-runtime")).toHaveCount(0);
});
