import { expect, type Page, test } from "@playwright/test";

interface DatabaseSettingsData {
  adapter: "local" | "oci";
  user: string;
  dsn: string;
  wallet_dir: string;
  wallet_uploaded: boolean;
  available_services: string[];
  has_password: boolean;
  has_wallet_password: boolean;
  readiness: string;
  embedding_dimension: number;
  vector_column: string;
  config_source: "runtime";
}

const databaseSettings: DatabaseSettingsData = {
  adapter: "local",
  user: "rag_app",
  dsn: "adb.ap-osaka-1.oraclecloud.com/ragdb_high",
  wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
  wallet_uploaded: false,
  available_services: [],
  has_password: false,
  has_wallet_password: false,
  readiness: "missing_credentials",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  config_source: "runtime",
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({
      json: {
        data: {
          mode: "local",
          auth_required: false,
          authenticated: true,
          user: null,
          expires_at: null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
});

test("データベース設定から Wallet ZIP をアップロードできる", async ({ page }) => {
  let current = { ...databaseSettings };
  await mockDatabaseSettings(page, () => current, async () => {
    current = {
      ...current,
      wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
      wallet_uploaded: true,
      available_services: ["ragdb_high", "ragdb_low"],
      readiness: "ok",
    };
  });

  await page.goto("/settings/database");

  await expect(page.getByText("保存済みパスワードを削除する")).toHaveCount(0);
  await expect(page.getByText("保存済み Wallet パスワードを削除する")).toHaveCount(0);

  const walletDir = page.getByLabel("Wallet ディレクトリ");
  await expect(walletDir).toHaveValue("/u01/aipoc/instantclient_23_26/network/admin");
  await expect(walletDir).toHaveAttribute("readonly", "");
  await expect(page.getByText("Wallet ディレクトリが見つかりません。")).toBeVisible();
  await page.getByLabel("Wallet ZIP ファイルを選択").setInputFiles({
    name: "Wallet_RAGDB.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("wallet-zip"),
  });

  await expect(walletDir).toHaveValue("/u01/aipoc/instantclient_23_26/network/admin");
  await expect(
    page.getByText("Wallet ZIP をアップロードしました: Wallet_RAGDB.zip")
  ).toBeVisible();
  await expect(page.getByText("Wallet ディレクトリが見つかりません。")).toHaveCount(0);
  await expect(page.getByText("Readiness: OK")).toBeVisible();

  await page.getByRole("combobox", { name: "Wallet サービス名" }).click();
  await page.getByRole("option", { name: "ragdb_high" }).click();
  await expect(page.getByLabel("DSN / 接続文字列")).toHaveValue("ragdb_high");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 720, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`Wallet ZIP アップロード導線が収まる (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockDatabaseSettings(page, () => databaseSettings);

    await page.goto("/settings/database");

    const button = page.getByRole("button", { name: "Wallet ZIP を選択" });
    await expect(button).toBeVisible();
    const metrics = await button.evaluate((element) => ({
      clientHeight: element.clientHeight,
      clientWidth: element.clientWidth,
      offsetHeight: (element as HTMLElement).offsetHeight,
      scrollHeight: element.scrollHeight,
      scrollWidth: element.scrollWidth,
    }));
    expect(metrics.offsetHeight).toBeGreaterThanOrEqual(44);
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth + 1);
    expect(metrics.scrollHeight).toBeLessThanOrEqual(metrics.clientHeight + 1);
  });
}

async function mockDatabaseSettings(
  page: Page,
  getCurrent: () => DatabaseSettingsData,
  onWalletUpload?: () => Promise<void>
) {
  await page.route("**/api/settings/database**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/settings/database/wallet") {
      await onWalletUpload?.();
      await route.fulfill({
        json: {
          data: getCurrent(),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    await route.fulfill({
      json: {
        data: getCurrent(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}
