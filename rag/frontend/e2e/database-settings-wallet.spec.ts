import { expect, type Page, test } from "@playwright/test";

interface DatabaseSettingsData {
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
  adb_ocid: string;
  region: string;
  config_source: "runtime";
}

interface DatabaseConnectionTestResult {
  status: "success" | "failed";
  readiness: string;
  message: string;
  elapsed_ms: number;
  troubleshooting: string[];
  details: Record<string, string | number | boolean | null>;
  checked_at: string;
  error_type: string | null;
}

const databaseSettings: DatabaseSettingsData = {
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
  adb_ocid: "",
  region: "ap-osaka-1",
  config_source: "runtime",
};

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }

    await route.continue();
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
  await expect(page.getByRole("button", { name: "再読み込み" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "データベース設定" }).first()).toBeVisible();
  await expect(page.getByLabel("データベースユーザー")).toHaveValue("rag_app");
  await expect(page.getByLabel("データベースパスワード")).toBeVisible();
  await expect(page.getByText("Wallet状態:")).toBeVisible();
  await expect(page.locator("form").getByText("未設定", { exact: true }).first()).toBeVisible();
  await expect(
    page.getByText("Wallet保存先: /u01/aipoc/instantclient_23_26/network/admin")
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: ".env プレビュー" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "運用メモ" })).toBeVisible();
  await expect(page.getByLabel(".env プレビュー")).toContainText("ORACLE_USER=rag_app");
  await expect(page.getByText("認証方式")).toBeVisible();
  await expect(page.getByText("アダプタ")).toHaveCount(0);
  await expect(page.getByText("反映先")).toHaveCount(0);
  await expect(page.getByText("サービス名候補")).toHaveCount(0);
  await expect(page.getByText("Embedding 次元")).toHaveCount(0);
  await expect(page.getByText("ベクトル列")).toHaveCount(0);
  await page.getByLabel("Wallet ZIP ファイルを選択").setInputFiles({
    name: "Wallet_RAGDB.zip",
    mimeType: "application/zip",
    buffer: Buffer.from("wallet-zip"),
  });

  await expect(
    page.getByText("Wallet ZIP をアップロードしました: Wallet_RAGDB.zip")
  ).toBeVisible();
  await expect(page.locator("form").getByText("Readiness: OK")).toBeVisible();
  await expect(page.getByText("サービス名候補")).toHaveCount(0);
  await expect(page.getByText("Embedding 次元")).toHaveCount(0);
  await expect(page.getByText("ベクトル列")).toHaveCount(0);
  const walletService = page.getByRole("combobox", { name: "サービス名 / DSN" });
  await walletService.click();
  await page.getByRole("option", { name: "ragdb_high" }).click();
  await expect(walletService).toContainText("ragdb_high");
  await expect(page.getByLabel(".env プレビュー")).toContainText("ORACLE_DSN=ragdb_high");
});

test("保存済み DB 認証 secret をチェックボックスで削除できる", async ({ page }) => {
  let savedPayload: Record<string, unknown> | null = null;
  await mockDatabaseSettings(
    page,
    () => ({
      ...databaseSettings,
      has_password: true,
      has_wallet_password: true,
      readiness: "ok",
    }),
    undefined,
    async (payload) => {
      savedPayload = payload;
    }
  );

  await page.goto("/settings/database");

  const password = page.getByLabel("データベースパスワード", { exact: true });
  await expect(page.getByText("保存済みパスワードを削除する")).toBeVisible();
  await expect(page.getByText("保存済み Wallet パスワードを削除する")).toHaveCount(0);

  await page.getByLabel("保存済みパスワードを削除する").check();
  await expect(password).toBeDisabled();

  await page.getByRole("button", { name: "DB設定を保存", exact: true }).click();

  expect(savedPayload).toMatchObject({
    clear_password: true,
  });
  expect(savedPayload).not.toHaveProperty("password");
});

test("接続テストのタイムアウト診断を表示できる", async ({ page }) => {
  await mockDatabaseSettings(
    page,
    () => ({
      ...databaseSettings,
      wallet_uploaded: true,
      available_services: ["ragdb_high"],
      readiness: "ok",
    }),
    undefined,
    undefined,
    async () => ({
      status: "failed",
      readiness: "ok",
      message: "Oracle 26ai 接続テストが 15 秒でタイムアウトしました。",
      elapsed_ms: 15001,
      troubleshooting: [
        "接続テストがタイムアウトしました。ADB が起動中か、TCPS 1522 に到達できるか確認してください。",
      ],
      details: { timeout_seconds: 15, tcp_connect_timeout_seconds: 10 },
      checked_at: "2026-06-14T00:00:00Z",
      error_type: "OracleConnectionTimeoutError",
    })
  );

  await page.goto("/settings/database");
  await page.getByRole("button", { name: "DB接続テスト" }).click();

  await expect(page.getByText("Oracle 26ai 接続テストが 15 秒でタイムアウトしました。")).toBeVisible();
  await expect(page.getByText(/所要時間: 15001 ms/)).toBeVisible();
  await expect(page.getByText(/TCPS 1522/)).toBeVisible();
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

    const button = page.getByRole("button", { name: /Wallet ファイルをアップロード/ });
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
  onWalletUpload?: () => Promise<void>,
  onSave?: (payload: Record<string, unknown>) => Promise<void>,
  onTest?: () => Promise<DatabaseConnectionTestResult>
) {
  await page.route("**/api/settings/database**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/settings/database/adb")) {
      await route.fulfill({
        json: {
          data: {
            status: "not_configured",
            message: "ADB OCID が設定されていません。",
            id: null,
            display_name: null,
            lifecycle_state: null,
            db_name: null,
            cpu_core_count: null,
            data_storage_size_in_tbs: null,
            region: "ap-osaka-1",
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

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

    if (url.pathname === "/api/settings/database/test") {
      await route.fulfill({
        json: {
          data:
            (await onTest?.()) ?? {
              status: "success",
              readiness: "ok",
              message: "Oracle 26ai への接続に成功しました。",
              elapsed_ms: 12,
              troubleshooting: [],
              details: { timeout_seconds: 15, tcp_connect_timeout_seconds: 10 },
              checked_at: "2026-06-14T00:00:00Z",
              error_type: null,
            },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (url.pathname === "/api/settings/database" && route.request().method() === "PATCH") {
      await onSave?.(JSON.parse(route.request().postData() || "{}"));
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
