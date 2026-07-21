import { expect, test, type Page, type Route } from "@playwright/test";
import { systemAdminMe } from "./_helpers/database-gate";

function envelope(data: unknown, errors: string[] = []) {
  return { data, error_messages: errors, warning_messages: [] };
}

async function fulfill(route: Route, data: unknown, status = 200, errors: string[] = []) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(envelope(data, errors)),
  });
}

const databaseSettings = {
  user: "NL2SQL_APP",
  dsn: "nl2sqldb_high",
  wallet_dir: "/wallet",
  wallet_uploaded: true,
  available_services: ["nl2sqldb_high"],
  has_password: true,
  has_wallet_password: false,
  readiness: "ok",
  embedding_dimension: 1536,
  vector_column: "VECTOR(1536, FLOAT32)",
  adb_ocid: "ocid1.autonomousdatabase.oc1.ap-osaka-1.example",
  region: "ap-osaka-1",
  config_source: "runtime",
};

const adbInfo = {
  status: "success",
  message: "ADB OCID が設定されています。",
  id: databaseSettings.adb_ocid,
  display_name: "nl2sqldb",
  lifecycle_state: "AVAILABLE",
  db_name: "NL2SQLDB",
  cpu_core_count: 2,
  data_storage_size_in_tbs: 1,
  region: "ap-osaka-1",
};

type SchemaStatus = "missing" | "partial" | "outdated" | "ready";

function systemTableRows(status: SchemaStatus, count: number) {
  const ready = status === "ready";
  const rows = [
    {
      name: "NL2SQL_PROFILES",
      exists: status !== "missing",
      estimated_rows: status === "missing" ? null : 12,
      created_at: status === "missing" ? null : "2026-07-19T00:00:00Z",
      last_analyzed_at: status === "missing" ? null : "2026-07-19T01:00:00Z",
    },
    {
      name: "NL2SQL_ONTOLOGY_REVISIONS",
      exists: ready,
      estimated_rows: ready ? 4 : null,
      created_at: ready ? "2026-07-19T00:00:00Z" : null,
      last_analyzed_at: null,
    },
  ];

  return Array.from({ length: count }, (_, index) =>
    rows[index] ?? {
      name: `NL2SQL_SYSTEM_TABLE_${String(index + 1).padStart(2, "0")}`,
      exists: ready,
      estimated_rows: ready ? index + 1 : null,
      created_at: ready ? "2026-07-19T00:00:00Z" : null,
      last_analyzed_at: ready ? "2026-07-19T01:00:00Z" : null,
    }
  );
}

function systemTables(
  status: SchemaStatus,
  options: { operationStatus?: string; tableCount?: number } = {}
) {
  const ready = status === "ready";
  const missingCount = status === "missing" ? 49 : status === "partial" ? 2 : 0;
  const operationStatus = options.operationStatus ?? "idle";
  return {
    status,
    schema_head: 6,
    applied_versions: ready ? [0, 1, 2, 3, 5, 6] : [0, 1, 2, 3],
    pending_versions: ready ? [] : [5, 6],
    expected_object_count: 49,
    existing_object_count: 49 - missingCount,
    missing_objects: Array.from({ length: missingCount }, (_, index) => ({
      name: `NL2SQL_MISSING_${index + 1}`,
      object_type: "TABLE",
    })),
    tables: systemTableRows(status, options.tableCount ?? 2),
    operation_state: {
      status: operationStatus,
      operation_kind: operationStatus === "running" ? "initialize" : null,
      lease_expires_at: operationStatus === "running" ? "2026-07-19T00:02:00Z" : null,
      last_error_code: operationStatus === "failed" ? "ORA-00600" : null,
      schema_epoch: 7,
      updated_at: "2026-07-19T00:00:00Z",
    },
  };
}

async function mockDatabasePage(page: Page, user = systemAdminMe) {
  await page.route("**/api/auth/me", (route) => fulfill(route, user));
  await page.route("**/api/ready/database", (route) =>
    fulfill(route, { status: "ok", check: "ok", detail: null })
  );
  await page.route("**/api/settings/database", (route) => fulfill(route, databaseSettings));
  await page.route("**/api/settings/database/adb", (route) => fulfill(route, adbInfo));
  await page.route("**/api/schema/owners", (route) =>
    fulfill(route, {
      current_owner: "NL2SQL_APP",
      owners: [
        { owner: "NL2SQL_APP", is_current: true, table_count: 49, view_count: 0 },
      ],
      excluded_oracle_maintained_count: 20,
    })
  );
}

async function expectNoPageOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
      )
    )
    .toBeTruthy();
}

test.beforeEach(async ({ page }) => {
  await mockDatabasePage(page);
});

test("状態取得中は aria status を表示し、完了後に操作を有効化する", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/settings/database/system-tables", async (route) => {
    await pending;
    await fulfill(route, systemTables("ready"));
  });

  await page.goto("/settings/database#system-tables");
  await expect(
    page.getByRole("status", { name: "システムテーブルの状態を読み込んでいます" })
  ).toBeVisible();
  release();
  await expect(page.locator("#system-tables").getByRole("button", { name: "作成・更新" })).toBeEnabled();
});

test("四つの schema 状態を再取得し、詳細表を局所スクロールで表示する", async ({ page }) => {
  let status: SchemaStatus = "missing";
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables(status))
  );

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await expect(card).toBeVisible();

  for (const [nextStatus, label] of [
    ["missing", "未初期化"],
    ["partial", "一部不足"],
    ["outdated", "更新必要"],
    ["ready", "初期化済み"],
  ] as const) {
    status = nextStatus;
    await card.getByRole("button", { name: "状態を再取得" }).click();
    await expect(
      card.locator("span").filter({ hasText: new RegExp(`^${label}$`) }).first()
    ).toBeVisible();
  }

  await card.getByText("システムテーブルの詳細を表示").click();
  await expect(card.getByRole("table")).toBeVisible();
  await expect(card.getByText("NL2SQL_PROFILES", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("詳細表は 10 行まで自然表示し、11 行目から内部スクロールする", async ({ page }, testInfo) => {
  let tableCount = 10;
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables("ready", { tableCount }))
  );

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await card.getByText("システムテーブルの詳細を表示").click();

  const scrollRegion = page.getByTestId("system-tables-scroll-region");
  await expect(scrollRegion).toHaveAttribute("role", "region");
  await expect(scrollRegion).toHaveAttribute(
    "aria-label",
    "システムテーブル一覧。必要に応じて縦方向または横方向にスクロールできます。"
  );
  await expect(scrollRegion.locator("tbody tr")).toHaveCount(10);

  const tenRows = await scrollRegion.evaluate((node) => ({
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
    overflowX: window.getComputedStyle(node).overflowX,
    overflowY: window.getComputedStyle(node).overflowY,
  }));
  expect(tenRows.scrollHeight).toBeLessThanOrEqual(tenRows.clientHeight + 1);
  expect(tenRows.overflowX).toBe("auto");
  expect(tenRows.overflowY).toBe("auto");

  tableCount = 11;
  await card.getByRole("button", { name: "状態を再取得" }).click();
  await expect(scrollRegion.locator("tbody tr")).toHaveCount(11);

  const overflowing = await scrollRegion.evaluate((node) => {
    const regionRect = node.getBoundingClientRect();
    const header = node.querySelector("thead");
    const rows = Array.from(node.querySelectorAll("tbody tr"));
    if (!header) throw new Error("system table header is missing");
    const headerRect = header.getBoundingClientRect();
    const visibleTop = headerRect.bottom;
    const visibleRows = rows.filter((row) => {
      const rect = row.getBoundingClientRect();
      return rect.top >= visibleTop - 1 && rect.bottom <= regionRect.bottom + 1;
    });
    return {
      clientHeight: node.clientHeight,
      scrollHeight: node.scrollHeight,
      scrollWidth: node.scrollWidth,
      clientWidth: node.clientWidth,
      visibleRowCount: visibleRows.length,
      headerPosition: window.getComputedStyle(header).position,
    };
  });
  expect(overflowing.scrollHeight).toBeGreaterThan(overflowing.clientHeight);
  expect(overflowing.visibleRowCount).toBe(10);
  expect(overflowing.headerPosition).toBe("sticky");
  if (testInfo.project.name === "mobile-375") {
    expect(overflowing.scrollWidth).toBeGreaterThan(overflowing.clientWidth);
  }

  await scrollRegion.focus();
  await expect(scrollRegion).toBeFocused();
  await page.keyboard.press("PageDown");
  await expect.poll(() => scrollRegion.evaluate((node) => node.scrollTop)).toBeGreaterThan(0);

  await scrollRegion.evaluate((node) => node.scrollTo({ top: node.scrollHeight }));
  const bottomState = await scrollRegion.evaluate((node) => {
    const regionRect = node.getBoundingClientRect();
    const header = node.querySelector("thead");
    const lastRow = node.querySelector("tbody tr:last-child");
    if (!header || !lastRow) throw new Error("system table rows are missing");
    const headerRect = header.getBoundingClientRect();
    const lastRowRect = lastRow.getBoundingClientRect();
    return {
      headerOffset: Math.abs(headerRect.top - regionRect.top),
      lastRowVisible:
        lastRowRect.top >= headerRect.bottom - 1 &&
        lastRowRect.bottom <= regionRect.bottom + 1,
    };
  });
  expect(bottomState.headerOffset).toBeLessThanOrEqual(1);
  expect(bottomState.lastRowVisible).toBe(true);
  await expectNoPageOverflow(page);
});

test("初期化中は重複操作を無効化し、成功後に Toast と ready 状態を表示する", async ({ page }) => {
  let status: SchemaStatus = "missing";
  let requestCount = 0;
  let requestBody: unknown = null;
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables(status))
  );
  await page.route("**/api/settings/database/system-tables/initialize", async (route) => {
    requestCount += 1;
    requestBody = route.request().postDataJSON();
    await new Promise((resolve) => setTimeout(resolve, 250));
    status = "ready";
    await fulfill(route, {
      ...systemTables("ready"),
      operation: "initialized",
      applied_versions: [0, 1, 2, 3, 5, 6],
      dropped_object_count: 0,
      created_object_count: 49,
    });
  });

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  const initialize = card.getByRole("button", { name: "作成・更新" });
  await initialize.click();
  await expect(initialize).toBeDisabled();
  await expect(card.getByRole("button", { name: "すべて再作成" })).toBeDisabled();
  await expect(card.getByRole("button", { name: "状態を再取得" })).toBeDisabled();
  await expect(page.getByText("システムテーブルを初期作成しました。")).toBeVisible();
  await expect(card.getByText("初期化済み", { exact: true })).toBeVisible();
  expect(requestCount).toBe(1);
  expect(requestBody).toEqual({ recreate: false });
});

test("全再作成は danger 確認、Esc・遮罩保護・焦点復帰を満たす", async ({ page }) => {
  let recreateRequests = 0;
  let requestBody: unknown = null;
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables("ready"))
  );
  await page.route("**/api/settings/database/system-tables/initialize", async (route) => {
    recreateRequests += 1;
    requestBody = route.request().postDataJSON();
    await fulfill(route, {
      ...systemTables("ready"),
      operation: "recreated",
      applied_versions: [0, 1, 2, 3, 5, 6],
      dropped_object_count: 48,
      created_object_count: 48,
    });
  });

  await page.goto("/settings/database#system-tables");
  const trigger = page.locator("#system-tables").getByRole("button", { name: "すべて再作成" });
  await trigger.focus();
  await trigger.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("認証/RBAC/DeepSec");
  await expect(dialog).toContainText("ユーザー業務表");

  await page.locator(".fixed.inset-0").click({ position: { x: 4, y: 4 } });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(recreateRequests).toBe(0);

  await trigger.click();
  await page.getByRole("alertdialog").getByRole("button", { name: "すべて再作成" }).click();
  await expect(page.getByText("システムテーブルをすべて再作成しました。")).toBeVisible();
  expect(recreateRequests).toBe(1);
  expect(requestBody).toEqual({
    recreate: true,
    confirmation: "RECREATE_NL2SQL_SYSTEM_TABLES",
  });
});

test("SQL 実行権限がない利用者は状態のみ閲覧できる", async ({ page }) => {
  await page.route("**/api/auth/me", (route) =>
    fulfill(route, {
      ...systemAdminMe,
      role_codes: ["DB_VIEWER"],
      permissions: ["settings.database.view"],
    })
  );
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables("ready"))
  );

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await expect(card.getByText(/管理 SQL 実行権限が必要/)).toBeVisible();
  await expect(card.getByRole("button", { name: "作成・更新" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "すべて再作成" })).toHaveCount(0);
  await expect(card.getByRole("button", { name: "状態を再取得" })).toBeVisible();
});

test("接続・操作失敗を操作領域で通知し、復旧方法を提示する", async ({ page }) => {
  let loadFails = true;
  await page.route("**/api/settings/database/system-tables", (route) =>
    loadFails
      ? fulfill(route, null, 503, ["Oracle に接続できませんでした (ORA-12514)。"])
      : fulfill(route, systemTables("partial"))
  );
  await page.route("**/api/settings/database/system-tables/initialize", (route) =>
    fulfill(route, null, 409, ["実行中の schema refresh job があります。"])
  );

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  await expect(card.getByText("データベースを起動してください", { exact: true })).toBeVisible();
  await expect(
    card.getByText(
      "データベースが起動していないか、ネットワーク経由で到達できません。データベースを起動してから再試行してください。接続情報の確認・変更もデータベース設定から行えます。",
      { exact: true }
    )
  ).toHaveCount(0);
  await expect(
    card.getByText(
      "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。",
      { exact: true }
    )
  ).toHaveCount(0);
  await expect(card.getByText("システムテーブルの状態を取得できません")).toHaveCount(0);
  await expect(card.getByText(/ORA-12514/)).toHaveCount(0);

  const settingsLink = card.getByRole("link", { name: "データベース設定を開く" });
  const retry = card.getByRole("button", { name: "再試行" });
  await expect(settingsLink).toHaveCount(0);
  await retry.focus();
  await expect(retry).toBeFocused();

  loadFails = false;
  await page.keyboard.press("Enter");
  await expect(
    card.locator("span").filter({ hasText: /^一部不足$/ }).first()
  ).toBeVisible();
  await card.getByRole("button", { name: "作成・更新" }).click();
  await expect(page.getByRole("alert")).toContainText("実行中の schema refresh job");
  await expect(page.getByRole("alert")).toContainText("状態を再取得してから再試行");
  await expectNoPageOverflow(page);
});
