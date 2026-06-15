import { expect, type Page, test } from "@playwright/test";

type AdbStatus =
  | "success"
  | "not_configured"
  | "error"
  | "accepted"
  | "already_available"
  | "already_stopped"
  | "cannot_start"
  | "cannot_stop";

interface AdbInfoData {
  status: AdbStatus;
  message: string;
  id: string | null;
  display_name: string | null;
  lifecycle_state: string | null;
  db_name: string | null;
  cpu_core_count: number | null;
  data_storage_size_in_tbs: number | null;
  region: string | null;
}

const databaseSettings = {
  user: "rag_app",
  dsn: "adb.ap-osaka-1.oraclecloud.com/ragdb_high",
  wallet_dir: "/u01/aipoc/instantclient_23_26/network/admin",
  wallet_uploaded: true,
  available_services: ["ragdb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  adb_ocid: "ocid1.autonomousdatabase.oc1..rag",
  region: "ap-osaka-1",
  config_source: "runtime" as const,
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

function adbInfo(overrides: Partial<AdbInfoData> = {}): AdbInfoData {
  return {
    status: "success",
    message: "データベース情報を取得しました。",
    id: "ocid1.autonomousdatabase.oc1..rag",
    display_name: "RAG ADB",
    lifecycle_state: "STOPPED",
    db_name: "ragdb",
    cpu_core_count: 2,
    data_storage_size_in_tbs: 1,
    region: "ap-osaka-1",
    ...overrides,
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

interface AdbMockHandlers {
  info: () => AdbInfoData;
  onStart?: () => AdbInfoData;
  onStop?: () => AdbInfoData;
  onSaveSettings?: (payload: Record<string, unknown>) => AdbInfoData;
}

async function mockDatabaseAndAdb(page: Page, handlers: AdbMockHandlers) {
  const envelope = (data: unknown) => ({
    json: { data, error_messages: [], warning_messages: [] },
  });

  await page.route("**/api/settings/database**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();

    if (url.pathname === "/api/settings/database/adb/start") {
      await route.fulfill(envelope(handlers.onStart?.() ?? handlers.info()));
      return;
    }
    if (url.pathname === "/api/settings/database/adb/stop") {
      await route.fulfill(envelope(handlers.onStop?.() ?? handlers.info()));
      return;
    }
    if (url.pathname === "/api/settings/database/adb/settings") {
      const payload = JSON.parse(route.request().postData() || "{}");
      await route.fulfill(envelope(handlers.onSaveSettings?.(payload) ?? handlers.info()));
      return;
    }
    if (url.pathname === "/api/settings/database/adb") {
      await route.fulfill(envelope(handlers.info()));
      return;
    }
    if (url.pathname === "/api/settings/database" && method === "GET") {
      await route.fulfill(envelope(databaseSettings));
      return;
    }

    await route.fulfill(envelope(databaseSettings));
  });
}

test("ADB 管理パネルが情報を表示し起動操作できる", async ({ page }) => {
  let started = false;
  await mockDatabaseAndAdb(page, {
    info: () => adbInfo({ lifecycle_state: started ? "STARTING" : "STOPPED" }),
    onStart: () => {
      started = true;
      return adbInfo({
        status: "accepted",
        message: "データベース 'RAG ADB' の起動を開始しました。",
        lifecycle_state: "STARTING",
      });
    },
  });

  await page.goto("/settings/database");

  await expect(
    page.getByRole("heading", { name: "Autonomous Database 管理" })
  ).toBeVisible();
  await expect(page.getByLabel("ADB OCID")).toHaveValue("ocid1.autonomousdatabase.oc1..rag");
  await expect(page.getByText("状態: 停止済み")).toBeVisible();

  await page.getByRole("button", { name: "起動", exact: true }).click();

  await expect(page.getByText("状態: 起動中")).toBeVisible();
  await expect(page.getByText("操作履歴")).toBeVisible();
  await expect(
    page.getByText("データベース 'RAG ADB' の起動を開始しました。")
  ).toBeVisible();
});

test("起動済み ADB を停止操作できる", async ({ page }) => {
  await mockDatabaseAndAdb(page, {
    info: () => adbInfo({ lifecycle_state: "AVAILABLE" }),
    onStop: () =>
      adbInfo({
        status: "accepted",
        message: "データベース 'RAG ADB' の停止を開始しました。",
        lifecycle_state: "STOPPING",
      }),
  });

  await page.goto("/settings/database");

  await expect(page.getByText("状態: 起動済み")).toBeVisible();

  await page.getByRole("button", { name: "停止", exact: true }).click();

  await expect(page.getByText("状態: 停止中")).toBeVisible();
  await expect(
    page.getByText("データベース 'RAG ADB' の停止を開始しました。")
  ).toBeVisible();
});

test("OCID 未入力では起動 / 停止できない", async ({ page }) => {
  await mockDatabaseAndAdb(page, {
    info: () =>
      adbInfo({
        status: "not_configured",
        message: "ADB OCID が設定されていません。",
        id: null,
        display_name: null,
        lifecycle_state: null,
        db_name: null,
        cpu_core_count: null,
        data_storage_size_in_tbs: null,
      }),
  });

  // OCID 空の DB 設定を返す
  await page.route("**/api/settings/database", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({
        json: {
          data: { ...databaseSettings, adb_ocid: "" },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/settings/database");

  await expect(
    page.getByRole("heading", { name: "Autonomous Database 管理" })
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "起動", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeDisabled();
});
