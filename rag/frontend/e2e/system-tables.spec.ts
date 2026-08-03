import { expect, test, type Page } from "@playwright/test";

type SchemaStatus = "missing" | "partial" | "outdated" | "ready";

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

const databaseSettings = {
  user: "rag_app",
  dsn: "ragdb_high",
  wallet_dir: "/wallet",
  wallet_uploaded: true,
  available_services: ["ragdb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  adb_ocid: "",
  region: "ap-osaka-1",
  config_source: "runtime",
};

function systemTables(status: SchemaStatus) {
  const complete = status === "ready" || status === "outdated";
  return {
    status,
    schema_version: "2",
    schema_head: "20260703_002_feedback_details",
    applied_versions: complete ? ["20260703_002_feedback_details"] : [],
    pending_versions: status === "outdated" ? ["20260703_002_feedback_details"] : [],
    expected_object_count: 97,
    existing_object_count: status === "missing" ? 0 : status === "partial" ? 54 : 97,
    expected_table_count: 28,
    existing_table_count: status === "missing" ? 0 : status === "partial" ? 18 : 28,
    missing_objects:
      status === "missing" || status === "partial"
        ? [{ name: "RAG_DOCUMENTS", object_type: "TABLE" }]
        : [],
    retired_objects: [],
    tables: [
      {
        name: "RAG_DOCUMENTS",
        exists: status !== "missing",
        estimated_rows: status === "ready" ? 12 : null,
        created_at: status === "missing" ? null : "2026-07-23T00:00:00+09:00",
        last_analyzed_at: null,
      },
      {
        name: "RAG_CHUNKS",
        exists: complete,
        estimated_rows: status === "ready" ? 240 : null,
        created_at: complete ? "2026-07-23T00:00:00+09:00" : null,
        last_analyzed_at: null,
      },
    ],
    operation_state: {
      status: "idle",
      operation_kind: null,
      lease_expires_at: null,
      last_error_code: null,
      schema_epoch: 3,
      updated_at: "2026-07-23T00:00:00+09:00",
    },
  };
}

async function mockSettings(
  page: Page,
  options: {
    initialStatus?: SchemaStatus;
    initializeFails?: boolean;
    statusDelayMs?: number;
  } = {}
) {
  let status = options.initialStatus ?? "missing";
  let initializeCalls = 0;
  let recreatePayload: Record<string, unknown> | null = null;

  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: authStatus })
  );
  await page.route("**/api/settings/database**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/settings/database/system-tables/initialize") {
      initializeCalls += 1;
      recreatePayload = JSON.parse(request.postData() ?? "{}");
      if (options.initializeFails) {
        await route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: ["Oracle の対象オブジェクトがロックされています。"],
            warning_messages: [],
            error_code: "ORA-00054",
          },
        });
        return;
      }
      status = "ready";
      await route.fulfill({
        json: {
          data: {
            ...systemTables("ready"),
            operation: recreatePayload.recreate ? "recreated" : "initialized",
            dropped_object_count: recreatePayload.recreate ? 96 : 0,
            created_object_count: 96,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (url.pathname === "/api/settings/database/system-tables") {
      if (options.statusDelayMs) {
        await new Promise((resolve) => setTimeout(resolve, options.statusDelayMs));
      }
      await route.fulfill({
        json: {
          data: systemTables(status),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (url.pathname === "/api/settings/database/adb") {
      await route.fulfill({
        json: {
          data: {
            status: "not_configured",
            message: "ADB OCID が未設定です。",
            id: null,
            display_name: null,
            lifecycle_state: null,
            db_name: null,
            cpu_core_count: null,
            data_storage_size_in_tbs: null,
            region: null,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: databaseSettings,
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  return {
    initializeCalls: () => initializeCalls,
    recreatePayload: () => recreatePayload,
  };
}

test("状態取得中は loading feedback を表示する", async ({ page }) => {
  await mockSettings(page, { statusDelayMs: 600 });
  await page.goto("/settings/database#system-tables");
  await expect(
    page.getByRole("status", {
      name: "システムテーブルの状態を確認しています。",
    })
  ).toBeVisible();
  await expect(page.locator("#system-tables").getByText("未作成").first()).toBeVisible();
});

const statusLabels: Record<SchemaStatus, string> = {
  missing: "未作成",
  partial: "一部不足",
  outdated: "更新が必要",
  ready: "準備完了",
};

for (const status of Object.keys(statusLabels) as SchemaStatus[]) {
  test(`schema 状態 ${status} を表示する`, async ({ page }) => {
    await mockSettings(page, { initialStatus: status });
    await page.goto("/settings/database#system-tables");
    const card = page.locator("#system-tables");
    await expect(
      card.getByText(statusLabels[status], { exact: true }).first()
    ).toBeVisible();
  });
}

test("作成・更新で missing から ready になる", async ({ page }) => {
  const mock = await mockSettings(page, { initialStatus: "missing" });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await card.getByRole("button", { name: "作成・更新" }).click();
  await expect(card.getByText("準備完了", { exact: true })).toBeVisible();
  expect(mock.initializeCalls()).toBe(1);
});

test("全再作成は確認語と ConfirmDialog の二段階で保護する", async ({
  page,
}) => {
  const mock = await mockSettings(page, { initialStatus: "ready" });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  const recreate = card.getByRole("button", { name: "すべて再作成" });
  await expect(recreate).toBeDisabled();

  await card.getByLabel("確認文字列").fill("RECREATE_RAG_SYSTEM_TABLES");
  await expect(recreate).toBeEnabled();
  await recreate.click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("RAG の DB データを削除しますか？");
  await dialog.getByRole("button", { name: "すべて再作成" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(card.getByText("準備完了", { exact: true })).toBeVisible();
  expect(mock.recreatePayload()).toEqual({
    recreate: true,
    confirmation: "RECREATE_RAG_SYSTEM_TABLES",
  });
});

test("操作失敗後にエラーへフォーカスし、375px でページ横溢れしない", async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await mockSettings(page, {
    initialStatus: "partial",
    initializeFails: true,
  });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await card.getByRole("button", { name: "作成・更新" }).click();

  const error = page.getByTestId("system-tables-operation-error");
  await expect(error).toBeFocused();
  await expect(error).toContainText("ロック");

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(pageOverflow).toBeLessThanOrEqual(1);

  await card.getByText("テーブルと migration の詳細").click();
  await card.getByTestId("system-tables-scroll-region").focus();
  await expect(card.getByTestId("system-tables-scroll-region")).toBeFocused();
});
