import { expect, test, type Page, type Route } from "@playwright/test";
import { systemAdminMe } from "./_helpers/database-gate";

function envelope(data: unknown, errors: string[] = [], errorCode?: string) {
  return {
    data,
    error_messages: errors,
    warning_messages: [],
    ...(errorCode ? { error_code: errorCode } : {}),
  };
}

async function fulfill(
  route: Route,
  data: unknown,
  status = 200,
  errors: string[] = [],
  errorCode?: string
) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(envelope(data, errors, errorCode)),
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
  options: {
    operationStatus?: string;
    tableCount?: number;
    lastErrorCode?: string;
  } = {}
) {
  const ready = status === "ready";
  const missingCount = status === "missing" ? 53 : status === "partial" ? 4 : 0;
  const operationStatus = options.operationStatus ?? "idle";
  const missingObjects =
    status === "partial"
      ? [
          { name: "NL2SQL_EVALUATION_JOBS", object_type: "TABLE" },
          { name: "NL2SQL_EVALUATION_RESULTS", object_type: "TABLE" },
          { name: "IX_NL2SQL_EVAL_JOB_STATE", object_type: "INDEX" },
          { name: "IX_NL2SQL_EVAL_JOB_LEASE", object_type: "INDEX" },
        ]
      : Array.from({ length: missingCount }, (_, index) => ({
          name: `NL2SQL_MISSING_${index + 1}`,
          object_type: "TABLE",
        }));
  return {
    status,
    schema_head: 8,
    applied_versions: ready ? [0, 1, 2, 3, 5, 6, 7, 8] : [0, 1, 2, 3, 5, 6],
    pending_versions: ready ? [] : [7, 8],
    expected_object_count: 53,
    existing_object_count: 53 - missingCount,
    expected_table_count: 28,
    existing_table_count: ready ? 28 : Math.max(0, 28 - missingCount),
    missing_objects: missingObjects,
    tables: systemTableRows(status, options.tableCount ?? 2),
    operation_state: {
      status: operationStatus,
      operation_kind: operationStatus === "running" ? "initialize" : null,
      lease_expires_at: operationStatus === "running" ? "2026-07-19T00:02:00Z" : null,
      last_error_code:
        operationStatus === "failed"
          ? (options.lastErrorCode ?? "ORA-00600")
          : null,
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
    await expect(page.getByRole("status").filter({ hasText: "最新の状態に更新しました。" }).last()).toBeVisible();
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
      applied_versions: [0, 1, 2, 3, 5, 6, 7, 8],
      dropped_object_count: 0,
      created_object_count: 53,
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
  await expect(card.getByText("53 / 53", { exact: true })).toBeVisible();
  await card.getByText("システムテーブルの詳細を表示").click();
  await expect(card.getByText(/適用済み version: 0, 1, 2, 3, 5, 6, 7, 8/)).toBeVisible();
  await expect(
    page
      .getByRole("region", { name: "通知" })
      .getByRole("status")
      .filter({ hasText: "システムテーブルを初期作成しました。" })
  ).toHaveCount(1);
  expect(requestCount).toBe(1);
  expect(requestBody).toEqual({ recreate: false });
});

test("no-op Toast は文末で折り返し、通知領域・焦点・閉じる操作を統一する", async ({ page }) => {
  await page.route("**/api/settings/database/system-tables", (route) =>
    fulfill(route, systemTables("ready"))
  );
  await page.route("**/api/settings/database/system-tables/initialize", (route) =>
    fulfill(route, {
      ...systemTables("ready"),
      operation: "no_op",
      applied_versions: [0, 1, 2, 3, 5, 6, 7, 8],
      dropped_object_count: 0,
      created_object_count: 0,
    })
  );

  await page.goto("/settings/database#system-tables");
  const initialize = page.locator("#system-tables").getByRole("button", { name: "作成・更新" });
  await initialize.focus();
  await initialize.click();

  const region = page.getByRole("region", { name: "通知" });
  await expect(region).toHaveAttribute("aria-live", "polite");
  const toastStatus = region.getByRole("status");
  await expect(toastStatus).toContainText("システムテーブルは最新です。変更はありません。");
  expect(
    await page.evaluate(() => document.activeElement?.closest('[role="region"]') != null)
  ).toBe(false);

  const sentenceSegments = toastStatus.locator("[data-message-sentence]");
  await expect(sentenceSegments).toHaveCount(2);
  await expect(sentenceSegments.nth(0)).toHaveText("システムテーブルは最新です。");
  await expect(sentenceSegments.nth(1)).toHaveText("変更はありません。");
  const segmentTops = await sentenceSegments.evaluateAll((nodes) =>
    nodes.map((node) => Math.round(node.getBoundingClientRect().top))
  );
  expect(segmentTops[1]).toBeGreaterThan(segmentTops[0]);

  const close = toastStatus.getByRole("button", { name: "閉じる" });
  const closeBox = await close.boundingBox();
  expect(closeBox?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(closeBox?.height ?? 0).toBeGreaterThanOrEqual(44);
  await expectNoPageOverflow(page);

  const lightBackground = await toastStatus.evaluate((node) => getComputedStyle(node).backgroundColor);
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  const darkBackground = await toastStatus.evaluate((node) => getComputedStyle(node).backgroundColor);
  expect(darkBackground).not.toBe(lightBackground);

  await close.focus();
  await page.keyboard.press("Enter");
  await expect(toastStatus).toHaveCount(0);
});

test("全再作成は実行確認語の完全一致まで実行できない", async ({ page }) => {
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
      applied_versions: [0, 1, 2, 3, 5, 6, 7, 8],
      dropped_object_count: 52,
      created_object_count: 52,
    });
  });

  await page.goto("/settings/database#system-tables");
  const card = page.locator("#system-tables");
  const trigger = card.getByRole("button", { name: "すべて再作成" });
  const confirmationField = card.getByTestId("execution-confirmation-field");
  const field = card.getByRole("textbox", { name: "実行確認語" });

  // 未入力・不一致(ADMIN_EXECUTE を含む)では実行できない。
  await expect(trigger).toBeDisabled();
  await field.fill("ADMIN_EXECUTE");
  await expect(confirmationField.getByText("不一致")).toBeVisible();
  await expect(trigger).toBeDisabled();
  expect(recreateRequests).toBe(0);

  await field.fill("RECREATE_NL2SQL_SYSTEM_TABLES");
  await expect(confirmationField.getByText("確認済み")).toBeVisible();
  await expect(trigger).toBeEnabled();
  await trigger.click();
  await expect(page.getByText("システムテーブルをすべて再作成しました。")).toBeVisible();
  expect(recreateRequests).toBe(1);
  expect(requestBody).toEqual({
    recreate: true,
    confirmation: "RECREATE_NL2SQL_SYSTEM_TABLES",
  });
  // 成功後は確認語がクリアされ再度実行不可に戻る。
  await expect(trigger).toBeDisabled();
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
  let operationFailed = false;
  const longFailureDetail =
    `Oracle の対象オブジェクトのロックが 30 秒以内に解放されませんでした (ORA-00054)。` +
    `${"状態競合の原因を確認するための長い識別情報".repeat(24)}` +
    "実行中の schema refresh、Ontology、品質評価 job を完了または停止してから、状態を再取得して再試行してください。";
  await page.route("**/api/settings/database/system-tables", (route) =>
    loadFails
      ? fulfill(route, null, 503, ["Oracle に接続できませんでした (ORA-12514)。"])
      : fulfill(
          route,
          systemTables("partial", {
            operationStatus: operationFailed ? "failed" : "idle",
            lastErrorCode: "ORA-00054",
          })
        )
  );
  await page.route("**/api/settings/database/system-tables/initialize", (route) => {
    operationFailed = true;
    return fulfill(route, null, 409, [longFailureDetail], "ORA-00054");
  });

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
  const initialize = card.getByRole("button", { name: "作成・更新" });
  await initialize.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("system-tables-operation-error")).toBeFocused();
  const operationAlert = card.getByRole("alert");
  await expect(operationAlert).toHaveCount(1);
  await expect(operationAlert).toContainText("ORA-00054");
  await expect(operationAlert).toContainText("30 秒以内に解放されませんでした");
  await expect(operationAlert).toContainText("schema refresh、Ontology、品質評価 job");
  await expect(operationAlert).toContainText("状態を再取得して再試行");
  await expect(operationAlert).not.toContainText("前回の操作が完了していません");
  await expect(operationAlert.locator("[data-message-sentence]")).not.toHaveCount(0);
  await expectNoPageOverflow(page);
});
