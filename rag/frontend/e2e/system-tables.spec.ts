import { expect, test, type Page } from "@playwright/test";
import { mockAuthUser, mockLocalAuth } from "./_helpers";

type SchemaStatus = "missing" | "partial" | "outdated" | "ready";

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

const chunkSetsDocumentForeignKey = {
  name: "RAG_CHUNK_SETS_DOCUMENT_FK",
  table_name: "RAG_CHUNK_SETS",
  columns: ["DOCUMENT_ID"],
  referenced_table_name: "RAG_DOCUMENTS",
  referenced_columns: ["DOCUMENT_ID"],
  delete_rule: "CASCADE",
  orphan_rows: 0,
};

async function mockSettings(
  page: Page,
  options: {
    initialStatus?: SchemaStatus;
    initializeFails?: boolean;
    statusDelayMs?: number;
    /** 古い版の表に外部キーが無い（#505）。更新後は参照先のない行が残る警告を返す。 */
    foreignKeyDrift?: boolean;
  } = {}
) {
  let status = options.initialStatus ?? "missing";
  let initializeCalls = 0;
  let recreatePayload: Record<string, unknown> | null = null;

  await mockLocalAuth(page);
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
            orphaned_foreign_keys: options.foreignKeyDrift
              ? [{ ...chunkSetsDocumentForeignKey, orphan_rows: 240 }]
              : [],
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
          data:
            options.foreignKeyDrift && status === "outdated"
              ? {
                  ...systemTables(status),
                  pending_versions: [],
                  missing_foreign_keys: [
                    { ...chunkSetsDocumentForeignKey, orphan_rows: 240 },
                    {
                      ...chunkSetsDocumentForeignKey,
                      name: "RAG_DOC_EXT_DOCUMENT_FK",
                      table_name: "RAG_DOCUMENT_EXTRACTIONS",
                      orphan_rows: 0,
                    },
                  ],
                }
              : systemTables(status),
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
  // 3 製品共通のカード（#325）: 経過時間付きの読み込み表示と Skeleton。
  await expect(
    page.getByRole("region", { name: "システムテーブルの状態を読み込んでいます" })
  ).toBeVisible();
  await expect(page.locator("#system-tables").getByText("未初期化").first()).toBeVisible();
});

const statusLabels: Record<SchemaStatus, string> = {
  missing: "未初期化",
  partial: "一部不足",
  outdated: "更新必要",
  ready: "初期化済み",
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
  await expect(card.getByText("初期化済み", { exact: true })).toBeVisible();
  expect(mock.initializeCalls()).toBe(1);
});

test("システムテーブル管理の権限が無い利用者は状態だけを確認でき、作成・再作成を出さない", async ({
  page,
}) => {
  const mock = await mockSettings(page, { initialStatus: "missing" });
  // データベース設定の画面権限だけを持つ DB ユーザー（rag.system_tables.manage なし。#214）。
  await mockAuthUser(page, { permissions: ["menu.settings_database"] });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await expect(card.getByText("未初期化", { exact: true }).first()).toBeVisible();
  await expect(
    card.getByText("システムテーブルの作成・更新と全再作成には「システムテーブル管理」の権限が必要です。", {
      exact: false,
    })
  ).toBeVisible();
  await expect(card.getByRole("button", { name: "作成・更新" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "すべて再作成" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "状態を再取得" })).toBeVisible();
  expect(mock.initializeCalls()).toBe(0);
});

test("全再作成は確認語と ConfirmDialog の二段階で保護する", async ({
  page,
}) => {
  const mock = await mockSettings(page, { initialStatus: "ready" });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  const recreate = card.getByRole("button", { name: "すべて再作成" });
  await expect(recreate).toBeDisabled();

  await card.getByRole("textbox", { name: "実行確認語" }).fill("RECREATE_RAG_SYSTEM_TABLES");
  await expect(recreate).toBeEnabled();
  await recreate.click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("RAG の DB データを削除しますか？");
  await dialog.getByRole("button", { name: "すべて再作成" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(card.getByText("初期化済み", { exact: true })).toBeVisible();
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

test("不足している外部キーを更新必要として並べ、更新後は参照先のない行を警告する（#505）", async ({
  page,
}) => {
  const mock = await mockSettings(page, { initialStatus: "outdated", foreignKeyDrift: true });
  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await expect(card.getByText("更新必要", { exact: true }).first()).toBeVisible();
  const missing = card.getByTestId("system-tables-missing-foreign-keys");
  await expect(missing).toContainText("既存のテーブルに外部キーが 2 件ありません");
  await expect(missing).toContainText("RAG_CHUNK_SETS (DOCUMENT_ID) → RAG_DOCUMENTS");
  await expect(missing).toContainText("参照先のない行 240 件");
  await expect(missing).toContainText("RAG_DOCUMENT_EXTRACTIONS (DOCUMENT_ID) → RAG_DOCUMENTS");

  await card.getByRole("button", { name: "作成・更新" }).click();
  await expect(card.getByText("初期化済み", { exact: true })).toBeVisible();
  expect(mock.initializeCalls()).toBe(1);
  await expect(missing).toHaveCount(0);
  const orphaned = card.getByTestId("system-tables-orphaned-foreign-keys");
  await expect(orphaned).toContainText("既存の行は自動では削除しません");
  await expect(orphaned).toContainText("参照先のない行 240 件");

  const pageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth
  );
  expect(pageOverflow).toBeLessThanOrEqual(1);
});
