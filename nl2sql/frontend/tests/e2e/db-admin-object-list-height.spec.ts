import { expect, test, type Locator, type Page, type Route, type TestInfo } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

function createRequestGate() {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function clickPageHeaderAction(page: Page, testId: string, name: string) {
  const actions = page.getByTestId(testId);
  await expect(actions).toBeVisible();
  const visibleButton = actions.getByRole("button", { name, exact: true });
  if (await visibleButton.isVisible()) {
    await visibleButton.click();
    return;
  }
  await actions.getByRole("button", { name: "その他の操作", exact: true }).click();
  await page.getByRole("menuitem", { name, exact: true }).click();
}

async function expectNoHorizontalScroll(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
}

async function expectEqualFilterWidths(search: Locator, owner: Locator) {
  await expect
    .poll(async () => {
      const [searchBox, ownerBox] = await Promise.all([search.boundingBox(), owner.boundingBox()]);
      if (!searchBox || !ownerBox) return Number.POSITIVE_INFINITY;
      return Math.abs(searchBox.width - ownerBox.width);
    })
    .toBeLessThanOrEqual(2);
}

async function expectFloatingMenuWithoutVerticalScrollbar(menu: Locator) {
  await expect
    .poll(() =>
      menu.evaluate((node) => ({
        constrained: node.getAttribute("data-floating-menu-constrained"),
        fitsWithoutScrollbar: node.scrollHeight <= node.clientHeight + 1,
      }))
    )
    .toEqual({ constrained: null, fitsWithoutScrollbar: true });
}

async function pickerRowNames(list: Locator) {
  return list.getByRole("listitem").evaluateAll((rows) =>
    rows.map((row) => row.querySelector("button span")?.textContent?.trim() ?? "")
  );
}

async function expectContentActionsRightAligned(actions: Locator) {
  const metrics = await actions.evaluate((node) => {
    const group = node.querySelector('[role="group"]');
    const firstButton = group?.querySelector("button");
    const panel = node.closest('[role="tabpanel"]');
    const code = panel?.querySelector("pre");
    if (!group || !firstButton || !panel || !code) return null;
    const buttonRect = firstButton.getBoundingClientRect();
    const groupRect = group.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const codeRect = code.getBoundingClientRect();
    return {
      firstButtonLeft: buttonRect.left,
      codeLeft: codeRect.left,
      groupLeft: groupRect.left,
      groupRight: groupRect.right,
      panelLeft: panelRect.left,
      panelRight: panelRect.right,
    };
  });
  expect(metrics).not.toBeNull();
  expect(metrics!.groupRight).toBeGreaterThanOrEqual(metrics!.panelRight - 20);
  expect(metrics!.groupLeft).toBeGreaterThan(metrics!.panelLeft);
  expect(metrics!.firstButtonLeft).toBeGreaterThan(metrics!.codeLeft);
}

const scenarios = [
  {
    title: "テーブル一覧",
    path: "/table-management",
    apiPath: "**/api/nl2sql/db-admin/tables",
    prefix: "TABLE",
    objectType: "table",
  },
  {
    title: "ビュー一覧",
    path: "/view-management",
    apiPath: "**/api/nl2sql/db-admin/views",
    prefix: "VIEW",
    objectType: "view",
  },
] as const;

const metadataScenarios = [
  {
    title: "コメント管理",
    path: "/comment-management",
    idPrefix: "comment-management",
  },
  {
    title: "アノテーション管理",
    path: "/annotation-management",
    idPrefix: "annotation-management",
  },
] as const;

const fixedTargetVisibleRows = 5;

type MockAdminObjectItem = {
  name: string;
  owner: string;
  qualified_name?: string;
  object_type: "table" | "view";
  row_count: number | null;
  comment: string;
};

const sampleProfile = {
  id: "default",
  name: "既定プロファイル",
  category: "既定",
  description: "管理画面標準化の表示確認",
  allowed_tables: ["TABLE_01"],
  allowed_views: ["VIEW_01"],
  glossary: { 売上: "SALES.AMOUNT" },
  sql_rules: ["SELECT のみ"],
  default_row_limit: 100,
  safety_policy: "select_only",
  few_shot_examples: [{ question: "売上を見せて", sql: "SELECT * FROM TABLE_01" }],
  select_ai_config: {
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    region: "ap-osaka-1",
    model: "cohere.command-r-plus",
    embedding_model: "cohere.embed-v4.0",
    max_tokens: 32000,
    enforce_object_list: true,
    comments: true,
    annotations: false,
    constraints: false,
  },
  archived: false,
};

const selectAiProfile = {
  name: "NL2SQL_DEFAULT_PROFILE",
  status: "ENABLED",
  owner: "APP",
  created_at: "2026-06-21T10:00:00.000Z",
  description: "Default profile",
  category: "既定",
  object_list: ["TABLE_01", "VIEW_01"],
  tables: ["TABLE_01"],
  views: ["VIEW_01"],
  region: "ap-osaka-1",
  model: "cohere.command-r-plus",
  embedding_model: "cohere.embed-v4.0",
  attributes: { object_list: ["TABLE_01", "VIEW_01"] },
};

const managementCatalog = {
  refreshed_at: "2026-06-21T10:00:00.000Z",
  tables: [
    {
      table_name: "TABLE_01",
      logical_name: "テーブル01",
      owner: "APP",
      table_type: "TABLE",
      comment: "標準化テーブル",
      row_count: 10,
      columns: [],
      constraints: [],
    },
    {
      table_name: "VIEW_01",
      logical_name: "ビュー01",
      owner: "APP",
      table_type: "VIEW",
      comment: "標準化ビュー",
      row_count: null,
      columns: [],
      constraints: [],
    },
  ],
};

const tableObjects = {
  runtime: "deterministic",
  items: [
    { name: "TABLE_01", owner: "APP", object_type: "table", row_count: 10, comment: "標準化テーブル" },
  ],
  warnings: [],
};

const viewObjects = {
  runtime: "deterministic",
  items: [
    { name: "VIEW_01", owner: "APP", object_type: "view", row_count: null, comment: "標準化ビュー" },
  ],
  warnings: [],
};

const prepareManagementScenarios = [
  {
    title: "データ管理",
    path: "/data-management",
    idPrefix: "data-management",
    tabs: [
      { id: "preview", tabName: "テーブル・ビューデータの表示" },
      { id: "csv", tabName: "Excel/CSV アップロード(既存テーブル)" },
      { id: "synthetic", tabName: "合成データ生成" },
    ],
  },
  {
    title: "検証用サンプルデータ",
    path: "/sample-data",
    idPrefix: "sample-data",
    tabs: [
      { id: "import", tabName: "取り込み実行" },
      { id: "delete", tabName: "削除実行" },
    ],
  },
] as const;

async function mockObjectManagementApi(
  page: Page,
  scenario: (typeof scenarios)[number],
  options: { itemCount?: number; columnCount?: number; extraItems?: MockAdminObjectItem[] } = {}
) {
  const itemCount = options.itemCount ?? 30;
  const columns = Array.from({ length: options.columnCount ?? 0 }, (_, index) => ({
    column_name: `COLUMN_${String(index + 1).padStart(2, "0")}`,
    logical_name: `列 ${index + 1}`,
    data_type: index % 3 === 0 ? "VARCHAR2(200)" : "NUMBER",
    nullable: index % 2 === 0,
    comment: `列情報の高さ確認 ${index + 1}`,
    sample_values: [`sample-${index + 1}`],
  }));
  const generatedItems: MockAdminObjectItem[] = Array.from({ length: itemCount }, (_, index) => ({
    name: `${scenario.prefix}_${String(index + 1).padStart(2, "0")}`,
    owner: "APP",
    qualified_name: `APP.${scenario.prefix}_${String(index + 1).padStart(2, "0")}`,
    object_type: scenario.objectType,
    row_count: null,
    comment: "",
  }));
  const items = [...generatedItems, ...(options.extraItems ?? [])];
  const typeItems = items.filter((item) => item.object_type === scenario.objectType);

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [
        {
          owner: "APP",
          is_current: true,
          table_count: typeItems.filter((item) => item.object_type === "table").length,
          view_count: typeItems.filter((item) => item.object_type === "view").length,
        },
      ],
      excluded_oracle_maintained_count: 0,
    })
  );
  await page.route(scenario.apiPath, (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: typeItems,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: scenario.objectType === "table" ? items.length : 0,
      view_count: scenario.objectType === "view" ? items.length : 0,
      next_cursor: null,
      refreshed_at: "2026-06-21T10:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "TABLE_01");
    return fulfillJson(route, {
      name,
      owner: "APP",
      qualified_name: `APP.${name}`,
      object_type: "table",
      row_count: null,
      comment: "",
      columns,
      ddl: `CREATE TABLE "${name}" ("ID" NUMBER)`,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/views/*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "VIEW_01");
    return fulfillJson(route, {
      name,
      owner: "APP",
      qualified_name: `APP.${name}`,
      object_type: "view",
      row_count: null,
      comment: "",
      columns,
      ddl: `CREATE OR REPLACE VIEW "${name}" AS SELECT 1 AS ID FROM DUAL`,
      warnings: [],
    });
  });
}

async function mockMetadataManagementApi(
  page: Page,
  options: {
    empty?: boolean;
    extraTableItems?: MockAdminObjectItem[];
    extraViewItems?: MockAdminObjectItem[];
  } = {}
) {
  const baseTableItems: MockAdminObjectItem[] = options.empty
    ? []
    : Array.from({ length: 20 }, (_, index) => ({
        name: `META_TABLE_${String(index + 1).padStart(2, "0")}`,
        owner: "APP",
        object_type: "table",
        row_count: index,
        comment: `コメント対象テーブル ${index + 1}`,
      }));
  const baseViewItems: MockAdminObjectItem[] = options.empty
    ? []
    : Array.from({ length: 10 }, (_, index) => ({
        name: `META_VIEW_${String(index + 1).padStart(2, "0")}`,
        owner: "APP",
        object_type: "view",
        row_count: null,
        comment: `コメント対象ビュー ${index + 1}`,
      }));
  const tableItems = [...baseTableItems, ...(options.extraTableItems ?? [])];
  const viewItems = [...baseViewItems, ...(options.extraViewItems ?? [])];
  const catalog = {
    refreshed_at: "2026-06-21T10:00:00.000Z",
    tables: [],
  };

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, catalog));
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [
        { owner: "APP", is_current: true, table_count: tableItems.length, view_count: viewItems.length },
      ],
      excluded_oracle_maintained_count: 0,
    })
  );
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, catalog));
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: tableItems,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: viewItems,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const items = [...tableItems, ...viewItems];
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: tableItems.length,
      view_count: viewItems.length,
      next_cursor: null,
      refreshed_at: catalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
}

async function mockDataManagementApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/nl2sql/db-admin/tables", (route) => fulfillJson(route, tableObjects));
  await page.route("**/api/nl2sql/db-admin/views", (route) => fulfillJson(route, viewObjects));
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const items = [...tableObjects.items, ...viewObjects.items];
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: tableObjects.items.length,
      view_count: viewObjects.items.length,
      next_cursor: null,
      refreshed_at: managementCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [selectAiProfile], warnings: [] })
  );
}

async function mockSortableDataManagementApi(page: Page) {
  const items = [
    { name: "Z_PAYMENTS", owner: "BILLING", object_type: "table", row_count: 20, comment: "支払一覧" },
    { name: "A_VIEW", owner: "APP", object_type: "view", row_count: null, comment: "ビュー対象" },
    { name: "M_EMPTY", owner: "APP", object_type: "table", row_count: 0, comment: "空テーブル" },
    { name: "B_AUDIT", owner: "APP", object_type: "table", row_count: 3, comment: "監査ログ" },
  ];
  const tableItems = items.filter((item) => item.object_type === "table");
  const viewItems = items.filter((item) => item.object_type === "view");

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: tableItems, warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: viewItems, warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const url = new URL(route.request().url());
    const type = url.searchParams.get("type") ?? "all";
    const sourceItems = type === "table" ? tableItems : type === "view" ? viewItems : items;
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: sourceItems,
      total: sourceItems.length,
      table_count: type === "view" ? 0 : tableItems.length,
      view_count: type === "table" ? 0 : viewItems.length,
      next_cursor: null,
      refreshed_at: managementCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    });
  });
}

async function mockPagedDbAdminObjectsApi(page: Page) {
  const tableItems = Array.from({ length: 149 }, (_, index) => ({
    name: `PAGED_TABLE_${String(index + 1).padStart(3, "0")}`,
    owner: "APP",
    object_type: "table",
    row_count: index + 1,
    comment: `ページング対象テーブル ${index + 1}`,
  }));
  const viewItems = Array.from({ length: 2 }, (_, index) => ({
    name: `PAGED_VIEW_${String(index + 1).padStart(3, "0")}`,
    owner: "APP",
    object_type: "view",
    row_count: null,
    comment: `ページング対象ビュー ${index + 1}`,
  }));

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [
        { owner: "APP", is_current: true, table_count: tableItems.length, view_count: viewItems.length },
      ],
      excluded_oracle_maintained_count: 0,
    })
  );
  await page.route("**/api/schema/refresh", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const url = new URL(route.request().url());
    const type = url.searchParams.get("type") ?? "all";
    const cursor = url.searchParams.get("cursor") ?? "";
    const sourceItems =
      type === "table" ? tableItems : type === "view" ? viewItems : [...tableItems, ...viewItems];
    const items = cursor ? sourceItems.slice(100) : sourceItems.slice(0, 100);
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: sourceItems.length,
      table_count: type === "view" ? 0 : type === "table" ? sourceItems.length : tableItems.length,
      view_count: type === "table" ? 0 : type === "view" ? sourceItems.length : viewItems.length,
      next_cursor: !cursor && sourceItems.length > 100 ? "paged-db-admin-objects-2" : null,
      refreshed_at: "2026-06-21T10:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/PAGED_TABLE_*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "PAGED_TABLE_001");
    return fulfillJson(route, {
      name,
      owner: "APP",
      object_type: "table",
      row_count: 1,
      comment: "ページング対象テーブル",
      columns: [],
      ddl: "",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/views/PAGED_VIEW_*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "PAGED_VIEW_001");
    return fulfillJson(route, {
      name,
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "ページング対象ビュー",
      columns: [],
      ddl: "",
      warnings: [],
    });
  });
}

async function mockOwnerFilterDbAdminObjectsApi(page: Page) {
  const items = [
    {
      name: "ORDERS_TABLE_01",
      owner: "APP",
      object_type: "table",
      row_count: 3,
      comment: "受注テーブル",
    },
    {
      name: "LEDGER_TABLE_01",
      owner: "BILLING",
      object_type: "table",
      row_count: 7,
      comment: "元帳テーブル",
    },
    {
      name: "ORDERS_VIEW_01",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "受注ビュー",
    },
    {
      name: "LEDGER_VIEW_01",
      owner: "BILLING",
      object_type: "view",
      row_count: null,
      comment: "元帳ビュー",
    },
  ];
  const tableItems = items.filter((item) => item.object_type === "table");
  const viewItems = items.filter((item) => item.object_type === "view");

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [
        { owner: "APP", is_current: true, table_count: 1, view_count: 1 },
        { owner: "BILLING", is_current: false, table_count: 1, view_count: 1 },
      ],
      excluded_oracle_maintained_count: 0,
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const url = new URL(route.request().url());
    const type = url.searchParams.get("type") ?? "all";
    const ownerPrefix = (url.searchParams.get("owner_prefix") ?? "").toUpperCase();
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const sourceItems =
      type === "table" ? tableItems : type === "view" ? viewItems : items;
    const filteredItems = sourceItems.filter((item) => {
      if (ownerPrefix && !item.owner.startsWith(ownerPrefix)) return false;
      return !query || `${item.name} ${item.comment}`.toLowerCase().includes(query);
    });
    return fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: filteredItems,
      total: filteredItems.length,
      table_count: type === "view" ? 0 : filteredItems.filter((item) => item.object_type === "table").length,
      view_count: type === "table" ? 0 : filteredItems.filter((item) => item.object_type === "view").length,
      next_cursor: null,
      refreshed_at: "2026-06-21T10:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "ORDERS_TABLE_01");
    const owner = new URL(route.request().url()).searchParams.get("owner") ?? "APP";
    return fulfillJson(route, {
      name,
      owner,
      object_type: "table",
      row_count: name.startsWith("BILLING") ? 7 : 3,
      comment: "",
      columns: [],
      ddl: "",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/views/*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "ORDERS_VIEW_01");
    const owner = new URL(route.request().url()).searchParams.get("owner") ?? "APP";
    return fulfillJson(route, {
      name,
      owner,
      object_type: "view",
      row_count: null,
      comment: "",
      columns: [],
      ddl: "",
      warnings: [],
    });
  });
}

async function mockSampleDataApi(page: Page) {
  await page.route("**/api/nl2sql/sample-data", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      profile_id: "default",
      confirmation: "SQL_ASSIST_SAMPLE",
      objects: ["TABLE_01", "VIEW_01"],
      imported_objects: ["TABLE_01"],
      sql: {
        tables: ['CREATE TABLE "TABLE_01" ("ID" NUMBER)'],
        views: ['CREATE VIEW "VIEW_01" AS SELECT 1 AS ID FROM DUAL'],
        data: ['INSERT INTO "TABLE_01" ("ID") VALUES (1)'],
        delete: ['DROP TABLE "TABLE_01"'],
      },
      warnings: [],
    })
  );
}

async function mockProfileManagementApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/nl2sql/db-admin/views", (route) => fulfillJson(route, viewObjects));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, [sampleProfile]));
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [selectAiProfile], warnings: [] })
  );
}

async function mockPrepareManagementApi(page: Page, path: string) {
  if (path === "/data-management") {
    await mockDataManagementApi(page);
    return;
  }
  if (path === "/sample-data") {
    await mockSampleDataApi(page);
    return;
  }
  if (path === "/profiles") {
    await mockProfileManagementApi(page);
  }
}

async function expectSingleLine(locator: Locator) {
  const lineCount = await locator.evaluate((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0);
    range.detach();
    return rects.length;
  });
  expect(lineCount).toBeLessThanOrEqual(1);
}

function expectedObjectListRows(testInfo: TestInfo) {
  return testInfo.project.name === "mobile-375" ? 5 : 8;
}

async function expectObjectListRowLimit(
  list: Locator,
  rowSelector: string,
  visibleRows: number,
  options: { expectedHeightRem?: number; headerHeightRem?: number; rowHeightRem?: number } = {}
) {
  const fit = await list.evaluate(
    (node, { rowSelector: selector, visibleRows: limit }) => {
      const listBox = node.getBoundingClientRect();
      const rows = Array.from(node.querySelectorAll(selector)).map((row) =>
        row.getBoundingClientRect()
      );
      const computed = window.getComputedStyle(node);
      const maxHeight = Number.parseFloat(computed.maxHeight);
      const rootFontSize = Number.parseFloat(
        window.getComputedStyle(document.documentElement).fontSize
      );
      const limitRow = rows[limit - 1];
      const nextRow = rows[limit];
      return {
        listHeight: listBox.height,
        maxHeight,
        rootFontSize,
        firstInside: rows[0].top >= listBox.top - 1 && rows[0].bottom <= listBox.bottom + 1,
        limitInside: Boolean(limitRow && limitRow.bottom <= listBox.bottom + 1),
        nextBelow: Boolean(nextRow && nextRow.bottom > listBox.bottom + 1),
        lastBelow: rows[rows.length - 1].bottom > listBox.bottom + 1,
      };
    },
    { rowSelector, visibleRows }
  );

  const expectedMaxHeight =
    fit.rootFontSize *
    (options.expectedHeightRem ??
      (options.headerHeightRem ?? 2.5) + (options.rowHeightRem ?? 3.5) * visibleRows);
  expect(fit.maxHeight).toBeGreaterThanOrEqual(expectedMaxHeight - 2);
  expect(fit.maxHeight).toBeLessThanOrEqual(expectedMaxHeight + 2);
  expect(Math.abs(fit.listHeight - fit.maxHeight)).toBeLessThanOrEqual(2);
  expect(fit.firstInside).toBe(true);
  expect(fit.limitInside).toBe(true);
  expect(fit.nextBelow).toBe(true);
  expect(fit.lastBelow).toBe(true);
}

async function topLevelPanelStyle(page: Page, id: string, idPrefix = "table-management") {
  return page.locator(`#${idPrefix}-panel-${id}`).evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      backgroundColor: computed.backgroundColor,
      borderTopColor: computed.borderTopColor,
      borderTopWidth: computed.borderTopWidth,
      borderRadius: computed.borderRadius,
      boxShadow: computed.boxShadow,
      display: computed.display,
      gap: computed.gap,
      paddingTop: computed.paddingTop,
    };
  });
}

async function compactVisualStyle(locator: Locator) {
  return locator.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      alignItems: computed.alignItems,
      backgroundColor: computed.backgroundColor,
      borderTopColor: computed.borderTopColor,
      borderTopWidth: computed.borderTopWidth,
      color: computed.color,
      display: computed.display,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      gap: computed.gap,
      minHeight: computed.minHeight,
      paddingTop: computed.paddingTop,
      paddingBottom: computed.paddingBottom,
    };
  });
}

for (const scenario of scenarios) {
  test(
    `${scenario.title}はデスクトップ8行・モバイル5行の高さに収める`,
    async ({ page }, testInfo) => {
      await mockObjectManagementApi(page, scenario);
      await page.goto(scenario.path);

      const list = page.getByTestId("db-admin-object-list");
      const grid = page.getByTestId(`${scenario.objectType}-management-grid`);
      const detailHeader = page.getByTestId(`${scenario.objectType}-management-detail-header`);
      await expect(grid.locator("tbody tr")).toHaveCount(30);
      await expect(grid.locator("tbody tr").first()).toHaveAttribute("data-selected", "true");
      await expect(detailHeader).toContainText(`${scenario.prefix}_01`);

      await expectObjectListRowLimit(list, "tbody tr", expectedObjectListRows(testInfo));

      await page.getByRole("searchbox", { name: "検索" }).fill(`${scenario.prefix}_02`);
      await expect(grid.locator("tbody tr")).toHaveCount(1);
      await expect(grid.locator("tbody tr").first()).toHaveAttribute("data-selected", "true");
      await expect(detailHeader).toContainText(`${scenario.prefix}_02`);
    }
  );

  test(
    `${scenario.title}の列情報はデスクトップ8行・モバイル5行の高さで内部スクロールする`,
    async ({ page }, testInfo) => {
      await mockObjectManagementApi(page, scenario, { columnCount: 30 });
      await page.goto(scenario.path);

      const detailColumns = page.getByTestId("db-admin-detail-columns");
      await expect(detailColumns.locator("tbody tr")).toHaveCount(30);
      await expectObjectListRowLimit(detailColumns, "tbody tr", expectedObjectListRows(testInfo));

      const scrollState = await detailColumns.evaluate((node) => {
        node.scrollTop = node.scrollHeight;
        const regionRect = node.getBoundingClientRect();
        const header = node.querySelector("thead");
        if (!header) throw new Error("column detail header is missing");
        const headerRect = header.getBoundingClientRect();
        return {
          headerOffset: Math.abs(headerRect.top - regionRect.top),
          headerPosition: window.getComputedStyle(header).position,
          scrollTop: node.scrollTop,
          scrollHeight: node.scrollHeight,
          clientHeight: node.clientHeight,
        };
      });
      expect(scrollState.scrollHeight).toBeGreaterThan(scrollState.clientHeight);
      expect(scrollState.scrollTop).toBeGreaterThan(0);
      expect(scrollState.headerOffset).toBeLessThanOrEqual(1);
      expect(scrollState.headerPosition).toBe("sticky");
      await expectNoHorizontalScroll(page);
    }
  );

  test(`${scenario.title}は共通検索フィールドで絞り込める`, async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const search = page.getByRole("searchbox", { name: "検索" });
    await expect(search).toHaveAttribute("placeholder", "名前・コメントを入力");
    await expect(search.locator("xpath=ancestor::label").locator("svg.lucide-search")).toBeVisible();

    const grid = page.getByTestId(`${scenario.objectType}-management-grid`);
    await search.fill("_01");
    await expect(grid.locator("tbody tr")).toHaveCount(1);
    await expect(grid.getByText(`APP.${scenario.prefix}_01`, { exact: true })).toBeVisible();
    await search.clear();
    await expect(grid.locator("tbody tr")).toHaveCount(30);
  });

  test(`${scenario.title}は NL2SQL_ システム object を一覧に表示しない`, async ({ page }) => {
    await mockObjectManagementApi(page, scenario, {
      itemCount: 1,
      extraItems: [
        {
          name: "NL2SQL_SCHEMA_OBJECTS",
          owner: "APP",
          qualified_name: "APP.NL2SQL_SCHEMA_OBJECTS",
          object_type: scenario.objectType,
          row_count: null,
          comment: "system object",
        },
        {
          name: `${scenario.prefix}_OWNER_OK`,
          owner: "NL2SQL_APP",
          qualified_name: `NL2SQL_APP.${scenario.prefix}_OWNER_OK`,
          object_type: scenario.objectType,
          row_count: null,
          comment: "business object under NL2SQL_APP",
        },
        {
          name: `TD_NL2SQL_${scenario.prefix}`,
          owner: "APP",
          qualified_name: `APP.TD_NL2SQL_${scenario.prefix}`,
          object_type: scenario.objectType,
          row_count: null,
          comment: "business object with middle token",
        },
      ],
    });
    await page.goto(scenario.path);

    const search = page.getByRole("searchbox", { name: "検索" });
    const grid = page.getByTestId(`${scenario.objectType}-management-grid`);
    await expect(grid.getByText(`APP.${scenario.prefix}_01`, { exact: true })).toBeVisible();
    await expect(
      grid.getByText(`NL2SQL_APP.${scenario.prefix}_OWNER_OK`, { exact: true })
    ).toBeVisible();
    await expect(grid.getByText(`APP.TD_NL2SQL_${scenario.prefix}`, { exact: true })).toBeVisible();
    await expect(grid.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);

    await search.fill("NL2SQL_SCHEMA");
    await expect(grid.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
    await expect(grid.locator("tbody tr")).toHaveCount(0);
  });
}

for (const scenario of metadataScenarios) {
  test(`${scenario.title}は対象グリッドを5行の固定高さに収める`, async ({ page }) => {
    await mockMetadataManagementApi(page);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    await expect(page.getByTestId(`${scenario.idPrefix}-target-grid`).locator("tbody tr")).toHaveCount(30);

    await expectObjectListRowLimit(list, "tbody tr", fixedTargetVisibleRows);
  });

  test(`${scenario.title}は共通検索と所有者入力を種類フィルタの前に表示する`, async ({ page }) => {
    await mockOwnerFilterDbAdminObjectsApi(page);
    const ownerPrefixRequests: string[] = [];
    const exactOwnerRequests: string[] = [];
    const queryScopes: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/nl2sql/db-admin/objects") {
        ownerPrefixRequests.push(url.searchParams.get("owner_prefix") ?? "");
        queryScopes.push(url.searchParams.get("query_scope") ?? "");
        if (url.searchParams.has("owner")) exactOwnerRequests.push(url.searchParams.get("owner") ?? "");
      }
    });

    await page.goto(scenario.path);
    const toolbar = page.getByTestId(`${scenario.idPrefix}-target-toolbar`);
    const search = toolbar.getByRole("searchbox", { name: "検索" });
    const ownerFilter = toolbar.getByRole("searchbox", { name: "所有者" });
    await expect(search).toHaveAttribute("placeholder", "名前・コメントを入力");
    await expect(ownerFilter).toBeVisible();
    await expect(ownerFilter).toHaveAttribute("placeholder", "所有者の先頭を入力（例：ADM）");
    await expectEqualFilterWidths(search, ownerFilter);
    await expect(toolbar.getByRole("combobox", { name: "所有者" })).toHaveCount(0);
    await expect(toolbar.getByLabel("種類フィルタ")).toBeVisible();
    await search.focus();
    await page.keyboard.press("Tab");
    await expect(ownerFilter).toBeFocused();
    expect(await ownerFilter.evaluate((node) => getComputedStyle(node).boxShadow)).not.toBe("none");
    await search.fill("BILLING");
    await expect(page.getByText("条件に一致する対象がありません")).toBeVisible();
    await search.clear();
    await ownerFilter.fill("bil");
    await expect(ownerFilter).toHaveValue("BIL");
    await expect.poll(() => ownerPrefixRequests.includes("BIL")).toBe(true);
    await expect.poll(() => queryScopes.includes("name_comment")).toBe(true);
    expect(exactOwnerRequests).toEqual([]);
    await expect(page.getByTestId(`${scenario.idPrefix}-target-grid`).getByText("BILLING.LEDGER_TABLE_01", { exact: true })).toBeVisible();
    await expect(page.getByTestId(`${scenario.idPrefix}-target-grid`).getByText("APP.ORDERS_TABLE_01", { exact: true })).toHaveCount(0);
    await ownerFilter.fill("ZZZ");
    await expect(page.getByText("条件に一致する対象がありません")).toBeVisible();
    await ownerFilter.fill("");
    await expect(page.getByTestId(`${scenario.idPrefix}-target-grid`).getByText("APP.ORDERS_TABLE_01", { exact: true })).toBeVisible();
    await expectNoHorizontalScroll(page);
  });

  test(`${scenario.title}は NL2SQL_ システム object を対象 picker に表示しない`, async ({ page }) => {
    await mockMetadataManagementApi(page, {
      empty: true,
      extraTableItems: [
        {
          name: "NL2SQL_SCHEMA_OBJECTS",
          owner: "APP",
          object_type: "table",
          row_count: null,
          comment: "system table",
        },
        {
          name: "ORDERS",
          owner: "NL2SQL_APP",
          object_type: "table",
          row_count: null,
          comment: "business table",
        },
        {
          name: "TD_NL2SQL_ORDERS",
          owner: "APP",
          object_type: "table",
          row_count: null,
          comment: "business table",
        },
      ],
      extraViewItems: [
        {
          name: "NL2SQL_SYSTEM_VIEW",
          owner: "APP",
          object_type: "view",
          row_count: null,
          comment: "system view",
        },
      ],
    });
    await page.goto(scenario.path);

    const grid = page.getByTestId(`${scenario.idPrefix}-target-grid`);
    await expect(grid.getByText("NL2SQL_APP.ORDERS", { exact: true })).toBeVisible();
    await expect(grid.getByText("APP.TD_NL2SQL_ORDERS", { exact: true })).toBeVisible();
    await expect(grid.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
    await expect(grid.getByText("APP.NL2SQL_SYSTEM_VIEW", { exact: true })).toHaveCount(0);

    await page.getByRole("searchbox", { name: "検索" }).fill("NL2SQL_SCHEMA");
    await expect(grid.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
    await expect(grid.locator("tbody tr")).toHaveCount(0);
  });
}

test("合成データ生成は対象テーブル一覧を5行の固定高さに収める", async ({ page }) => {
  const syntheticTables = Array.from(
    { length: 8 },
    (_, index) => `APP.SYNTHETIC_TABLE_${String(index + 1).padStart(2, "0")}`
  );
  await mockDataManagementApi(page);
  await page.unroute("**/api/nl2sql/select-ai/db-profiles**");
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/NL2SQL_DEFAULT_PROFILE")) {
      return fulfillJson(route, {
        runtime: "deterministic",
        profile: {
          ...selectAiProfile,
          object_list: syntheticTables,
          attributes: { profile_attributes: { object_list: syntheticTables } },
        },
        warnings: [],
      });
    }
    return fulfillJson(route, { runtime: "deterministic", profiles: [selectAiProfile], warnings: [] });
  });

  await page.goto("/data-management");
  await page.getByRole("tab", { name: "合成データ生成" }).click();
  const syntheticPanel = page.locator("#data-management-panel-synthetic");
  await syntheticPanel.getByRole("button", { name: "テーブル一覧を取得" }).click();

  const list = page.getByTestId("data-synthetic-table-list");
  await expect(list.locator("label")).toHaveCount(syntheticTables.length);
  await expectObjectListRowLimit(list, "label", fixedTargetVisibleRows, { expectedHeightRem: 14 });
});

test("データ管理のテーブル・ビュー件数 badge は API 総数を表示する", async ({ page }) => {
  await mockPagedDbAdminObjectsApi(page);
  await page.goto("/data-management");

  const controls = page.locator("section[aria-labelledby='data-preview-controls-heading']");
  await expect(controls.getByRole("heading", { name: "テーブル・ビューデータの表示" })).toBeVisible();
  await expect(controls.getByText("全151件", { exact: true })).toBeVisible();
  await expect(controls.getByText("テーブル149", { exact: true })).toBeVisible();
  await expect(controls.getByText("ビュー2", { exact: true })).toBeVisible();
  await expect(page.getByTestId("data-preview-object-footer")).toContainText("100 / 151 件を表示");
});

test("データ管理の対象ピッカーはヘッダーで並び替えできる", async ({ page }, testInfo) => {
  await mockSortableDataManagementApi(page);
  await page.goto("/data-management");

  const previewList = page.getByTestId("data-preview-object-list");
  await expect(previewList).toBeVisible();
  await expectNoHorizontalScroll(page);

  if (testInfo.project.name === "mobile-375") {
    return;
  }

  await expect.poll(() => pickerRowNames(previewList)).toEqual([
    "APP.A_VIEW",
    "APP.B_AUDIT",
    "APP.M_EMPTY",
    "BILLING.Z_PAYMENTS",
  ]);
  await expect(previewList.getByRole("listitem").first()).toHaveAttribute("aria-current", "true");

  await previewList.getByRole("button", { name: /対象名/ }).click();
  await expect.poll(() => pickerRowNames(previewList)).toEqual([
    "BILLING.Z_PAYMENTS",
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "APP.A_VIEW",
  ]);
  await expect(previewList.getByRole("listitem").first()).toHaveAttribute("aria-current", "true");

  await previewList.getByRole("button", { name: /種類/ }).click();
  await expect.poll(() => pickerRowNames(previewList)).toEqual([
    "BILLING.Z_PAYMENTS",
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "APP.A_VIEW",
  ]);

  await previewList.getByRole("button", { name: /行数/ }).click();
  await expect.poll(() => pickerRowNames(previewList)).toEqual([
    "APP.A_VIEW",
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "BILLING.Z_PAYMENTS",
  ]);

  await previewList.getByRole("button", { name: /所有者/ }).click();
  await expect.poll(() => pickerRowNames(previewList)).toEqual([
    "APP.A_VIEW",
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "BILLING.Z_PAYMENTS",
  ]);

  await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
  const csvList = page.getByTestId("data-csv-table-list");
  await expect(csvList).toBeVisible();
  await expect.poll(() => pickerRowNames(csvList)).toEqual([
    "APP.B_AUDIT",
    "APP.M_EMPTY",
    "BILLING.Z_PAYMENTS",
  ]);
  await expect(csvList.getByRole("listitem").first()).toHaveAttribute("aria-current", "true");

  await csvList.getByRole("button", { name: /対象名/ }).click();
  await expect.poll(() => pickerRowNames(csvList)).toEqual([
    "BILLING.Z_PAYMENTS",
    "APP.M_EMPTY",
    "APP.B_AUDIT",
  ]);
  await expect(csvList.getByRole("listitem").first()).toHaveAttribute("aria-current", "true");

  await csvList.getByRole("button", { name: /行数/ }).click();
  await expect.poll(() => pickerRowNames(csvList)).toEqual([
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "BILLING.Z_PAYMENTS",
  ]);

  await csvList.getByRole("button", { name: /所有者/ }).click();
  await expect.poll(() => pickerRowNames(csvList)).toEqual([
    "APP.M_EMPTY",
    "APP.B_AUDIT",
    "BILLING.Z_PAYMENTS",
  ]);
});

test("データ管理の対象 picker は NL2SQL_ システム object を表示しない", async ({ page }) => {
  const items: MockAdminObjectItem[] = [
    {
      name: "ORDERS",
      owner: "APP",
      object_type: "table",
      row_count: 10,
      comment: "業務テーブル",
    },
    {
      name: "NL2SQL_SCHEMA_OBJECTS",
      owner: "APP",
      object_type: "table",
      row_count: null,
      comment: "system table",
    },
    {
      name: "NL2SQL_SYSTEM_VIEW",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "system view",
    },
    {
      name: "ORDERS",
      owner: "NL2SQL_APP",
      object_type: "table",
      row_count: 2,
      comment: "business owner table",
    },
    {
      name: "TD_NL2SQL_ORDERS",
      owner: "APP",
      object_type: "table",
      row_count: 1,
      comment: "middle-token business table",
    },
  ];
  const tableItems = items.filter((item) => item.object_type === "table");
  const viewItems = items.filter((item) => item.object_type === "view");

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: tableItems, warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: viewItems, warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: items.length,
      table_count: tableItems.length,
      view_count: viewItems.length,
      next_cursor: null,
      refreshed_at: managementCatalog.refreshed_at,
      catalog_version: 1,
      warnings: [],
    })
  );

  await page.goto("/data-management");

  const previewList = page.getByTestId("data-preview-object-list");
  await expect(previewList.getByText("APP.ORDERS", { exact: true })).toBeVisible();
  await expect(previewList.getByText("NL2SQL_APP.ORDERS", { exact: true })).toBeVisible();
  await expect(previewList.getByText("APP.TD_NL2SQL_ORDERS", { exact: true })).toBeVisible();
  await expect(previewList.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
  await expect(previewList.getByText("APP.NL2SQL_SYSTEM_VIEW", { exact: true })).toHaveCount(0);

  await page.getByRole("searchbox", { name: "検索" }).fill("NL2SQL_SCHEMA");
  await expect(previewList.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
  await expect(previewList.getByText("NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);

  await page.getByRole("searchbox", { name: "検索" }).clear();
  await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
  const csvList = page.getByTestId("data-csv-table-list");
  await expect(csvList.getByText("NL2SQL_APP.ORDERS", { exact: true })).toBeVisible();
  await expect(csvList.getByText("APP.TD_NL2SQL_ORDERS", { exact: true })).toBeVisible();
  await expect(csvList.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
});

test("コメント管理の対象件数 badge は footer と同じ API 総数を表示する", async ({ page }) => {
  await mockPagedDbAdminObjectsApi(page);
  await page.goto("/comment-management");

  const panel = page.locator("#comment-management-panel-targets");
  await expect(panel.getByRole("heading", { name: "対象選択" })).toBeVisible();
  await expect(panel.getByText("151 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("comment-management-target-footer")).toContainText("100 / 151 件を表示");
});

test("テーブル管理・ビュー管理の一覧件数 badge は API 総数を表示する", async ({ page }) => {
  await mockPagedDbAdminObjectsApi(page);
  await page.goto("/table-management");

  const tableGrid = page.locator("section[aria-labelledby='table-grid-heading']");
  await expect(tableGrid.getByText("149 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("table-management-footer")).toContainText("100 / 149 件を表示");

  await page.goto("/view-management");
  const viewGrid = page.locator("section[aria-labelledby='view-grid-heading']");
  await expect(viewGrid.getByText("2 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("view-management-footer")).toContainText("2 / 2 件を表示");
});

test("テーブル管理・ビュー管理は共通検索と所有者入力で一覧を絞り込む", async ({ page }) => {
  await mockOwnerFilterDbAdminObjectsApi(page);
  const ownerPrefixRequests: string[] = [];
  const exactOwnerRequests: string[] = [];
  const queryScopes: string[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/nl2sql/db-admin/objects") {
      ownerPrefixRequests.push(url.searchParams.get("owner_prefix") ?? "");
      queryScopes.push(url.searchParams.get("query_scope") ?? "");
      if (url.searchParams.has("owner")) exactOwnerRequests.push(url.searchParams.get("owner") ?? "");
    }
  });

  await page.goto("/table-management");
  const tableGrid = page.locator("section[aria-labelledby='table-grid-heading']");
  const tableSearch = tableGrid.getByRole("searchbox", { name: "検索" });
  const tableOwnerFilter = tableGrid.getByRole("searchbox", { name: "所有者" });
  await expect(tableSearch).toHaveAttribute("placeholder", "名前・コメントを入力");
  await expect(tableOwnerFilter).toHaveAttribute("placeholder", "所有者の先頭を入力（例：ADM）");
  await expectEqualFilterWidths(tableSearch, tableOwnerFilter);
  await expect(tableOwnerFilter).toBeVisible();
  await expect(tableGrid.getByRole("combobox", { name: "所有者" })).toHaveCount(0);
  await expect(tableGrid.getByLabel("行数フィルタ")).toHaveCount(0);
  await tableSearch.focus();
  await page.keyboard.press("Tab");
  await expect(tableOwnerFilter).toBeFocused();
  await expect(tableGrid.getByText("APP.ORDERS_TABLE_01", { exact: true })).toBeVisible();
  await expect(tableGrid.getByText("BILLING.LEDGER_TABLE_01", { exact: true })).toBeVisible();
  await tableSearch.fill("BILLING");
  await expect(tableGrid.getByText("条件に一致するテーブルがありません")).toBeVisible();
  await tableSearch.clear();
  await tableOwnerFilter.fill("bil");
  await expect(tableOwnerFilter).toHaveValue("BIL");
  await expect.poll(() => ownerPrefixRequests.includes("BIL")).toBe(true);
  await expect.poll(() => queryScopes.includes("name_comment")).toBe(true);
  expect(exactOwnerRequests).toEqual([]);
  await expect(tableGrid.getByText("BILLING.LEDGER_TABLE_01", { exact: true })).toBeVisible();
  await expect(tableGrid.getByText("APP.ORDERS_TABLE_01", { exact: true })).toHaveCount(0);
  await tableOwnerFilter.fill("ZZZ");
  await expect(tableGrid.getByText("条件に一致するテーブルがありません")).toBeVisible();
  await tableOwnerFilter.fill("");
  await expect(tableGrid.getByText("APP.ORDERS_TABLE_01", { exact: true })).toBeVisible();
  await expectNoHorizontalScroll(page);

  await page.goto("/view-management");
  const viewGrid = page.locator("section[aria-labelledby='view-grid-heading']");
  const viewSearch = viewGrid.getByRole("searchbox", { name: "検索" });
  const viewOwnerFilter = viewGrid.getByRole("searchbox", { name: "所有者" });
  await expect(viewOwnerFilter).toBeVisible();
  await expectEqualFilterWidths(viewSearch, viewOwnerFilter);
  await expect(viewGrid.getByRole("combobox", { name: "所有者" })).toHaveCount(0);
  await expect(viewGrid.getByLabel("行数フィルタ")).toHaveCount(0);
  const requestCountBeforeViewFilter = ownerPrefixRequests.length;
  await viewOwnerFilter.fill("bil");
  await expect.poll(() => ownerPrefixRequests.slice(requestCountBeforeViewFilter).includes("BIL")).toBe(true);
  await expect(viewGrid.getByText("BILLING.LEDGER_VIEW_01", { exact: true })).toBeVisible();
  await expect(viewGrid.getByText("APP.ORDERS_VIEW_01", { exact: true })).toHaveCount(0);
  await expectNoHorizontalScroll(page);
});

test("テーブル管理は 150% zoom 相当でもタブ・列名・操作ボタンを折り返さない", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  // 一覧が既定。作成/取込はタブではなくツールバーのアクションボタン。
  const actions = page.getByTestId("table-management-actions");
  await expect(actions.getByRole("button")).toHaveText([
    "テーブル作成",
    "Excel/CSV 取込(新規テーブル)",
    "表示を更新",
    "DB 構造を再取得",
  ]);
  await expect(actions.locator('[data-page-action-group="task"]')).toHaveCount(2);
  await expect(actions.locator('[data-page-action-group="utility"]')).toHaveCount(2);
  await expect(actions.locator('[data-page-action-group="utility"][data-page-action-group-start="true"]')).toBeVisible();
  const [createBox, importBox, refreshBox, schemaRefreshBox] = await Promise.all([
    actions.getByRole("button", { name: "テーブル作成" }).boundingBox(),
    actions.getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).boundingBox(),
    actions.getByRole("button", { name: "表示を更新" }).boundingBox(),
    actions.getByRole("button", { name: "DB 構造を再取得" }).boundingBox(),
  ]);
  expect(createBox).not.toBeNull();
  expect(importBox).not.toBeNull();
  expect(refreshBox).not.toBeNull();
  expect(schemaRefreshBox).not.toBeNull();
  expect(importBox!.x).toBeGreaterThan(createBox!.x);
  expect(refreshBox!.x).toBeGreaterThan(importBox!.x);
  expect(schemaRefreshBox!.x).toBeGreaterThan(refreshBox!.x);
  await expectSingleLine(actions.getByRole("button", { name: "テーブル作成" }).locator("span"));
  await expectSingleLine(actions.getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).locator("span"));
  // 詳細内タブ(列情報/DDL)は維持。
  await expectSingleLine(page.getByRole("tab", { name: "列情報" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "DDL" }).locator("span").last());

  const grid = page.getByTestId("table-management-grid");
  await expect(grid.locator("tbody tr")).toHaveCount(30);
  await expect(grid.getByRole("columnheader", { name: /コメント/ })).toHaveCount(0);
  await expectSingleLine(grid.getByRole("columnheader", { name: /所有者/ }).locator("span").first());
  const rowAction = grid.getByRole("button", { name: /操作: APP\.TABLE_01/ });
  await expect(rowAction).toBeVisible();
  await expect(grid.getByRole("button", { name: "詳細" })).toHaveCount(0);
  await expect(grid.getByRole("button", { name: "削除" })).toHaveCount(0);
  await rowAction.click();
  const menu = page.getByRole("menu");
  await expect(menu).toHaveAttribute("data-floating-menu-placement", "bottom");
  await expect(page.getByRole("menuitem", { name: "詳細" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "削除" })).toBeVisible();
  await page.keyboard.press("Escape");
  const secondRow = grid.locator("tbody tr").nth(1);
  await secondRow.locator("td").nth(1).click();
  await expect(secondRow).toHaveAttribute("data-selected", "true");

  const scroll = await page.getByTestId("db-admin-object-list").evaluate((node) => ({
    internalWidthStable: node.scrollWidth >= node.clientWidth,
    pageHorizontal:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1,
  }));
  expect(scroll.internalWidthStable).toBe(true);
  expect(scroll.pageHorizontal).toBe(false);
});

test("テーブル管理の行メニューは下端では上方向に開く", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const list = page.getByTestId("db-admin-object-list");
  await list.evaluate((node) => {
    node.scrollTop = node.scrollHeight;
  });

  const bottomRowAction = page.getByRole("button", { name: /操作: APP\.TABLE_30/ });
  await expect(bottomRowAction).toBeVisible();
  const bottomTriggerBox = await bottomRowAction.boundingBox();
  expect(bottomTriggerBox).not.toBeNull();
  await bottomRowAction.click();

  const bottomMenu = page.getByRole("menu");
  await expect(bottomMenu).toBeVisible();
  await expect(bottomMenu).toHaveAttribute("data-floating-menu-placement", "top");
  const bottomMenuBox = await bottomMenu.boundingBox();
  expect(bottomMenuBox).not.toBeNull();
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  expect(bottomMenuBox!.y + bottomMenuBox!.height).toBeLessThanOrEqual(bottomTriggerBox!.y + 1);
  expect(bottomMenuBox!.y).toBeGreaterThanOrEqual(0);
  expect(bottomMenuBox!.y + bottomMenuBox!.height).toBeLessThanOrEqual(viewport!.height + 1);
  await expectFloatingMenuWithoutVerticalScrollbar(bottomMenu);
});

test("テーブル管理の行メニューは少件数の一覧でも裁切されない", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0], { itemCount: 1 });
  await page.goto("/table-management");

  const shortList = page.getByTestId("db-admin-object-list");
  const shortListBox = await shortList.boundingBox();
  expect(shortListBox).not.toBeNull();
  const shortRowAction = page.getByRole("button", { name: /操作: APP\.TABLE_01/ });
  await expect(shortRowAction).toBeVisible();
  await shortRowAction.click();

  const shortMenu = page.getByRole("menu");
  await expect(shortMenu).toBeVisible();
  await expect(shortMenu).toHaveAttribute("data-floating-menu-placement", "bottom");
  const shortMenuBox = await shortMenu.boundingBox();
  expect(shortMenuBox).not.toBeNull();
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  expect(shortMenuBox!.y + shortMenuBox!.height).toBeGreaterThan(shortListBox!.y + shortListBox!.height);
  expect(shortMenuBox!.y).toBeGreaterThanOrEqual(0);
  expect(shortMenuBox!.y + shortMenuBox!.height).toBeLessThanOrEqual(viewport!.height + 1);
});

test("テーブル詳細は列タブでは DDL を取得せず、DDL タブ初回表示で後追い取得する", async ({
  page,
}) => {
  const detailUrls: string[] = [];
  const exactCountGate = createRequestGate();
  let catalogHit = false;
  await page.route("**/api/schema/catalog", (route) => {
    catalogHit = true;
    return fulfillJson(route, { refreshed_at: "2026-06-21T10:00:00.000Z", tables: [] });
  });
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        { name: "TABLE_01", owner: "APP", object_type: "table", row_count: null, comment: "" },
      ],
      refreshed_at: "2026-07-14T03:53:00+00:00",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: [
        { name: "TABLE_01", owner: "APP", object_type: "table", row_count: null, comment: "" },
      ],
      total: 1,
      table_count: 1,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-14T03:53:00+00:00",
      catalog_version: 1,
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
    const url = route.request().url();
    detailUrls.push(url);
    // 重い GET_DDL を伴う DDL は include_ddl=1 のときだけ返す(バックエンドの挙動を模す)。
    const withDdl = url.includes("include_ddl=1");
    const exactCount = url.includes("exact_count=1");
    if (exactCount) await exactCountGate.promise;
    return fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      // 既定は num_rows 統計、exact_count=1 のときだけ COUNT(*) 相当の正確値を返す。
      row_count: exactCount ? 999 : null,
      comment: "",
      // サンプル値は詳細応答が返す(catalog 全取得に依存しない)。
      columns: [
        {
          column_name: "STATUS",
          logical_name: "状態",
          data_type: "VARCHAR2(20)",
          nullable: false,
          comment: "",
          sample_values: ["NEW", "PAID"],
        },
      ],
      ddl: withDdl ? 'CREATE TABLE "TABLE_01" ("ID" NUMBER)' : "",
      warnings: [],
    });
  });

  await page.goto("/table-management");

  // 列タブ(既定)の初期表示は DDL 抜きで取得し、サンプル値は詳細応答由来で表示される。
  await expect(page.getByRole("heading", { name: "TABLE_01" })).toBeVisible();
  await expect(page.getByText("NEW, PAID")).toBeVisible();
  const detailHeader = page.getByTestId("table-management-detail-header");
  await expect(detailHeader.getByText("-", { exact: true })).toBeVisible(); // 未取得時も行数スロットを予約する
  expect(detailUrls.length).toBeGreaterThan(0);
  expect(detailUrls.every((url) => url.includes("include_ddl=0"))).toBe(true);
  expect(detailUrls.some((url) => url.includes("include_ddl=1"))).toBe(false);
  expect(detailUrls.some((url) => url.includes("exact_count=1"))).toBe(false);
  // catalog 全取得は行わない(サンプル値も取得日時も一覧/詳細で賄う)。
  expect(catalogHit).toBe(false);

  const headerBoxBefore = await detailHeader.boundingBox();
  expect(headerBoxBefore).not.toBeNull();
  const exactCountButton = page.getByRole("button", { name: "COUNT(*) で正確な行数を取得" });

  // 「正確な件数を取得」は loading 中も消えず、ヘッダー高さを変えず、完了後も再実行できる。
  await exactCountButton.click();
  await expect.poll(() => detailUrls.filter((url) => url.includes("exact_count=1")).length).toBe(1);
  await expect(exactCountButton).toBeVisible();
  await expect(exactCountButton).toBeDisabled();
  const headerBoxLoading = await detailHeader.boundingBox();
  expect(headerBoxLoading).not.toBeNull();
  expect(Math.abs(headerBoxLoading!.height - headerBoxBefore!.height)).toBeLessThanOrEqual(1);
  exactCountGate.release();
  await expect(page.getByText("999 行")).toBeVisible();
  await expect(exactCountButton).toBeVisible();
  await expect(exactCountButton).toBeEnabled();
  const headerBoxAfter = await detailHeader.boundingBox();
  expect(headerBoxAfter).not.toBeNull();
  expect(Math.abs(headerBoxAfter!.height - headerBoxBefore!.height)).toBeLessThanOrEqual(1);
  expect(detailUrls.some((url) => url.includes("exact_count=1"))).toBe(true);
  await exactCountButton.click();
  await expect.poll(() => detailUrls.filter((url) => url.includes("exact_count=1")).length).toBeGreaterThanOrEqual(2);

  // DDL タブを開くと include_ddl=1 で後追い取得し、DDL が表示される。
  await page.getByRole("tab", { name: "DDL" }).click();
  await expect(page.getByText('CREATE TABLE "TABLE_01" ("ID" NUMBER)')).toBeVisible();
  await expectContentActionsRightAligned(page.getByTestId("table-management-ddl-actions"));
  expect(detailUrls.some((url) => url.includes("include_ddl=1"))).toBe(true);
});

for (const scenario of scenarios) {
  test(`${scenario.title}の DDL 後追い取得中は詳細を保った局所スケルトンと経過時間を表示する`, async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    const objectName = `${scenario.prefix}_DDL_LOADING`;
    const loadingLabel =
      scenario.objectType === "table"
        ? "テーブルの DDL を取得しています"
        : "ビューの DDL を取得しています";
    const ddl =
      scenario.objectType === "table"
        ? `CREATE TABLE "${objectName}" ("ID" NUMBER)`
        : `CREATE OR REPLACE VIEW "${objectName}" AS SELECT 1 AS ID FROM DUAL`;
    const ddlGate = createRequestGate();
    let ddlRequestCount = 0;
    let markDdlStarted: () => void = () => undefined;
    const ddlStarted = new Promise<void>((resolve) => {
      markDdlStarted = resolve;
    });
    const objectItems = [
      {
        name: objectName,
        owner: "APP",
        object_type: scenario.objectType,
        row_count: 1,
        comment: "",
      },
    ];

    await page.route(scenario.apiPath, (route) =>
      fulfillJson(route, {
        runtime: "deterministic",
        items: objectItems,
        refreshed_at: "2026-07-23T00:00:00+00:00",
        warnings: [],
      }),
    );
    await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
      fulfillJson(route, {
        runtime: "deterministic",
        owner: "APP",
        items: objectItems,
        total: objectItems.length,
        table_count: scenario.objectType === "table" ? objectItems.length : 0,
        view_count: scenario.objectType === "view" ? objectItems.length : 0,
        next_cursor: null,
        refreshed_at: "2026-07-23T00:00:00+00:00",
        catalog_version: 1,
        warnings: [],
      }),
    );
    await page.route(`${scenario.apiPath}/*`, async (route) => {
      const withDdl = route.request().url().includes("include_ddl=1");
      if (withDdl) {
        ddlRequestCount += 1;
        markDdlStarted();
        await ddlGate.promise;
      }
      await fulfillJson(route, {
        name: objectName,
        owner: "APP",
        object_type: scenario.objectType,
        row_count: 1,
        comment: "",
        columns: [
          {
            column_name: "ID",
            logical_name: "識別子",
            data_type: "NUMBER",
            nullable: false,
            comment: "",
            sample_values: ["1"],
          },
        ],
        ddl: withDdl ? ddl : "",
        warnings: [],
      });
    });

    await page.goto(scenario.path);
    await expect(page.getByRole("heading", { name: objectName })).toBeVisible();
    await page.getByRole("tab", { name: "DDL" }).click();
    await ddlStarted;

    const idPrefix = scenario.path.slice(1);
    const skeleton = page.getByTestId(`${idPrefix}-ddl-skeleton`);
    await expect(skeleton).toBeVisible();
    await expect(skeleton).toHaveAttribute("aria-busy", "true");
    await expect(skeleton).toHaveAttribute("data-processing-placement", "tab");
    await expect(skeleton).toContainText(loadingLabel);
    await expect(skeleton.getByRole("timer")).toHaveAccessibleName(/経過時間 00:0\d/);
    await expect(page.getByRole("heading", { name: objectName })).toBeVisible();
    await expect(page.getByRole("tab", { name: "DDL" })).toBeVisible();
    await expect(page.getByRole("button", { name: "コピー" })).toHaveCount(0);
    expect(
      await skeleton.evaluate((element) => {
        const detailPanel = element.closest("section[aria-labelledby]");
        const tabList = detailPanel?.querySelector('[role="tablist"]');
        return Boolean(
          detailPanel &&
            tabList &&
            tabList.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING,
        );
      }),
    ).toBe(true);

    const skeletonHeights = await skeleton
      .getByTestId("db-management-skeleton-block")
      .evaluateAll((elements) =>
        elements.map((element) =>
          Math.round(Number.parseFloat(window.getComputedStyle(element).height)),
        ),
      );
    expect(skeletonHeights).toEqual([40, 288]);
    await expect(skeleton.getByTestId("db-management-skeleton-block").first()).toHaveCSS(
      "animation-name",
      "none",
    );
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
          document.body.scrollWidth > document.body.clientWidth + 1,
      ),
    ).toBe(false);

    ddlGate.release();
    await expect(skeleton).toHaveCount(0);
    await expect(page.getByText(ddl)).toBeVisible();

    await page.getByRole("tab", { name: "列情報" }).click();
    await page.getByRole("tab", { name: "DDL" }).click();
    await expect(page.getByText(ddl)).toBeVisible();
    expect(ddlRequestCount).toBe(1);
  });
}

test("明示的な一覧再取得はヘッダーではなくテーブル作業領域の先頭に表示する", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  const refreshGate = createRequestGate();
  let objectRequestCount = 0;
  let holdNextRefresh = false;
  let markRefreshStarted: () => void = () => undefined;
  const refreshStarted = new Promise<void>((resolve) => {
    markRefreshStarted = resolve;
  });
  const tableItems = [
    {
      name: "TABLE_REFRESH",
      owner: "APP",
      object_type: "table",
      row_count: 12,
      comment: "",
    },
  ];

  await page.route("**/api/nl2sql/db-admin/objects?*", async (route) => {
    objectRequestCount += 1;
    if (holdNextRefresh) {
      holdNextRefresh = false;
      markRefreshStarted();
      await refreshGate.promise;
    }
    await fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: tableItems,
      total: 1,
      table_count: 1,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-29T00:00:00.000Z",
      catalog_version: objectRequestCount,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) =>
    fulfillJson(route, {
      name: "TABLE_REFRESH",
      owner: "APP",
      object_type: "table",
      row_count: 12,
      comment: "",
      columns: [
        {
          column_name: "ID",
          logical_name: "識別子",
          data_type: "NUMBER",
          nullable: false,
          comment: "",
          sample_values: ["1"],
        },
      ],
      ddl: "",
      warnings: [],
    }),
  );

  await page.goto("/table-management");
  await expect(page.getByRole("heading", { name: "TABLE_REFRESH" })).toBeVisible();
  holdNextRefresh = true;
  await clickPageHeaderAction(page, "table-management-actions", "表示を更新");
  await refreshStarted;

  const processing = page.getByTestId("table-management-workspace-processing");
  await expect(processing).toBeVisible();
  await expect(processing).toHaveAttribute("data-processing-placement", "workspace");
  await expect(processing).toContainText("テーブル一覧を更新しています");
  await expect(page.getByTestId("table-management-actions-processing")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "TABLE_REFRESH" })).toBeVisible();

  const shell = page
    .getByTestId("management-panel-shell")
    .filter({ has: page.getByTestId("fixed-split-pane-table-management-list") });
  await expect(shell.getByTestId("table-management-workspace-processing")).toBeVisible();
  expect(
    await processing.evaluate((element) => {
      const split = document.querySelector(
        '[data-testid="fixed-split-pane-table-management-list"]',
      );
      return Boolean(
        split &&
          element.compareDocumentPosition(split) & Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }),
  ).toBe(true);

  await page.setViewportSize({ width: 375, height: 812 });
  await expectNoHorizontalScroll(page);
  await expect(processing).toHaveAttribute("data-processing-activity-icon", "none");
  await expect(processing.locator("svg.animate-spin")).toHaveCount(0);

  refreshGate.release();
  await expect(processing).toHaveCount(0);
});

test("DDL の遅延応答中に別テーブルを選ぶと旧 DDL を破棄する", async ({ page }) => {
  const ddlGate = createRequestGate();
  let markDdlStarted: () => void = () => undefined;
  const ddlStarted = new Promise<void>((resolve) => {
    markDdlStarted = resolve;
  });
  const tableNames = ["TABLE_A", "TABLE_B"];
  const tableItems = tableNames.map((name) => ({
    name,
    owner: "APP",
    object_type: "table",
    row_count: 1,
    comment: "",
  }));

  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: tableItems,
      refreshed_at: "2026-07-23T00:00:00+00:00",
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: tableItems,
      total: tableItems.length,
      table_count: tableItems.length,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-23T00:00:00+00:00",
      catalog_version: 1,
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
    const url = new URL(route.request().url());
    const tableName = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    const withDdl = url.searchParams.get("include_ddl") === "1";
    if (tableName === "TABLE_A" && withDdl) {
      markDdlStarted();
      await ddlGate.promise;
    }
    try {
      await fulfillJson(route, {
        name: tableName,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
        columns: [
          {
            column_name: `${tableName}_ID`,
            logical_name: `${tableName} の識別子`,
            data_type: "NUMBER",
            nullable: false,
            comment: "",
            sample_values: ["1"],
          },
        ],
        ddl: withDdl ? `CREATE TABLE "${tableName}" ("ID" NUMBER)` : "",
        warnings: [],
      });
    } catch {
      // TABLE_A の DDL は TABLE_B 選択時に client 側で abort されるため fulfill 不可でも正常。
    }
  });

  await page.goto("/table-management");
  await expect(page.getByRole("heading", { name: "TABLE_A" })).toBeVisible();
  await page.getByRole("tab", { name: "DDL" }).click();
  await ddlStarted;
  await expect(page.getByTestId("table-management-ddl-skeleton")).toBeVisible();

  await page.getByRole("button", { name: "TABLE_B を表示" }).click();
  await expect(page.getByRole("heading", { name: "TABLE_B" })).toBeVisible();
  await expect(page.getByTestId("db-admin-detail-columns")).toContainText("TABLE_B の識別子");

  ddlGate.release();
  await page.waitForTimeout(100);
  await expect(page.getByText('CREATE TABLE "TABLE_A" ("ID" NUMBER)')).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "TABLE_B" })).toBeVisible();
});

test("テーブル詳細は経過時間、遅延案内、切替リセット、取消、成功を同じ領域で扱う", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.clock.install({ time: new Date("2026-07-29T00:00:00.000Z") });
  const firstSlowGate = createRequestGate();
  const cancelledSlowGate = createRequestGate();
  const tableNames = ["TABLE_SLOW", "TABLE_FAST"];
  const tableItems = tableNames.map((name) => ({
    name,
    owner: "APP",
    object_type: "table",
    row_count: 1,
    comment: "",
  }));
  let slowAttempts = 0;

  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: tableItems,
      total: tableItems.length,
      table_count: tableItems.length,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-29T00:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
    const url = new URL(route.request().url());
    const tableName = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    if (tableName === "TABLE_SLOW") {
      slowAttempts += 1;
      if (slowAttempts === 1) await firstSlowGate.promise;
      if (slowAttempts === 2) await cancelledSlowGate.promise;
    }
    try {
      await fulfillJson(route, {
        name: tableName,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
        columns: [
          {
            column_name: `${tableName}_ID`,
            logical_name: `${tableName} の識別子`,
            data_type: "NUMBER",
            nullable: false,
            comment: "",
            sample_values: ["1"],
          },
        ],
        ddl: "",
        warnings: [],
      });
    } catch {
      // 切替または取消で client が破棄した旧 request は fulfill できなくても正常。
    }
  });

  await page.goto("/table-management");
  // 一覧は名前順に整列され、先頭(TABLE_FAST)が自動選択される。遅い詳細は明示的に選択する。
  await expect(page.getByRole("heading", { name: "TABLE_FAST" })).toBeVisible();
  await page.getByRole("button", { name: "TABLE_SLOW を表示" }).click();
  const skeleton = page.getByTestId("table-management-detail-skeleton");
  await expect(skeleton).toBeVisible();
  await expect(skeleton).toHaveAttribute("data-processing-placement", "panel");
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:00");
  await expect(skeleton.locator("svg.animate-spin")).toHaveCSS("animation-name", "none");

  await page.clock.fastForward(11_000);
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:11");
  await expect(skeleton).toContainText("通常より時間がかかっています");

  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1,
    ),
  ).toBe(false);

  // 処理中に別テーブルへ切り替えると旧 request を無効化し、新しい timer は 00:00 から始まる。
  await page.getByRole("button", { name: "TABLE_FAST を表示" }).click();
  await expect(page.getByRole("heading", { name: "TABLE_FAST" })).toBeVisible();
  firstSlowGate.release();
  await page.waitForTimeout(50);
  await expect(page.getByText("TABLE_SLOW の識別子")).toHaveCount(0);

  // 同じ対象を再度読み込み、キーボードで取消しても選択状態は保持する。
  await page.getByRole("button", { name: "TABLE_SLOW を表示" }).click();
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:00");
  const cancel = skeleton.getByRole("button", { name: "キャンセル" });
  await cancel.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "TABLE_SLOW を表示" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  await expect(skeleton).toHaveCount(0);
  cancelledSlowGate.release();

  // 通常 request の完了後は timer を残さず、結果へ自然に置き換える。
  await page.getByRole("button", { name: "TABLE_SLOW を表示" }).click();
  await expect(page.getByRole("heading", { name: "TABLE_SLOW" })).toBeVisible();
  await expect(page.getByRole("timer")).toHaveCount(0);
});

test("テーブル詳細の30秒 timeout は選択を保持して再試行できる", async ({ page }) => {
  await page.addInitScript(() => {
    const nativeTimeout = AbortSignal.timeout.bind(AbortSignal);
    Object.defineProperty(AbortSignal, "timeout", {
      configurable: true,
      value: (delay: number) => nativeTimeout(delay === 30_000 ? 150 : delay),
    });
  });
  const timeoutGate = createRequestGate();
  let attempts = 0;
  const objectName = "TABLE_TIMEOUT";
  const items = [
    { name: objectName, owner: "APP", object_type: "table", row_count: 1, comment: "" },
  ];

  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items,
      total: 1,
      table_count: 1,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-29T00:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
    attempts += 1;
    if (attempts === 1) await timeoutGate.promise;
    try {
      await fulfillJson(route, {
        name: objectName,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
        columns: [],
        ddl: "",
        warnings: [],
      });
    } catch {
      // timeout で破棄された最初の request は fulfill 不可でも正常。
    }
  });

  await page.goto("/table-management");
  await expect(page.getByTestId("table-management-detail-error")).toContainText(
    "30秒以内に完了しませんでした",
  );
  await expect(page.getByRole("button", { name: `${objectName} を表示` })).toHaveAttribute(
    "aria-current",
    "true",
  );

  timeoutGate.release();
  const retry = page.getByRole("button", { name: "再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: objectName })).toBeVisible();
  expect(attempts).toBe(2);
});

for (const scenario of scenarios) {
  test(`${scenario.title}の詳細エラーは局所表示され、キーボードで再試行できる`, async ({
    page,
  }) => {
    const objectName = `${scenario.prefix}_RETRY`;
    let detailAttempts = 0;
    // 一覧は増分 API(/db-admin/objects)から取得する。
    await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
      fulfillJson(route, {
        runtime: "deterministic",
        owner: "APP",
        items: [
          {
            name: objectName,
            owner: "APP",
            object_type: scenario.objectType,
            row_count: 1,
            comment: "",
          },
        ],
        total: 1,
        table_count: scenario.objectType === "table" ? 1 : 0,
        view_count: scenario.objectType === "view" ? 1 : 0,
        next_cursor: null,
        refreshed_at: "2026-07-21T00:00:00+00:00",
        catalog_version: 1,
        warnings: [],
      }),
    );
    await page.route(scenario.apiPath, (route) =>
      fulfillJson(route, {
        runtime: "deterministic",
        items: [
          {
            name: objectName,
            owner: "APP",
            object_type: scenario.objectType,
            row_count: 1,
            comment: "",
          },
        ],
        refreshed_at: "2026-07-21T00:00:00+00:00",
        warnings: [],
      }),
    );
    await page.route(`${scenario.apiPath}/*`, async (route) => {
      detailAttempts += 1;
      if (detailAttempts === 1) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ error_messages: ["詳細サービスが応答しませんでした。"] }),
        });
        return;
      }
      await fulfillJson(route, {
        name: objectName,
        owner: "APP",
        object_type: scenario.objectType,
        row_count: 1,
        comment: "",
        columns: [
          {
            column_name: "ID",
            logical_name: "識別子",
            data_type: "NUMBER",
            nullable: false,
            comment: "",
            sample_values: ["1"],
          },
        ],
        ddl: "",
        warnings: [],
      });
    });

    await page.goto(scenario.path);

    const errorRegion = page.getByTestId(`${scenario.path.slice(1)}-detail-error`);
    await expect(errorRegion.getByRole("alert")).toContainText("詳細の取得に失敗しました");
    await expect(page.locator("main").getByRole("alert")).toHaveCount(1);
    const retry = errorRegion.getByRole("button", { name: "再試行" });
    await retry.focus();
    await expect(retry).toBeFocused();
    await retry.press("Enter");

    await expect(page.getByRole("heading", { name: objectName })).toBeVisible();
    await expect(page.getByTestId("db-admin-detail-columns")).toContainText("識別子");
    expect(detailAttempts).toBe(2);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      ),
    ).toBe(false);
  });
}

test("テーブル詳細は A→B→A の遅延応答でも最後に選んだ A だけを表示する", async ({
  page,
}) => {
  const tableNames = ["TABLE_A", "TABLE_B"];
  let releaseTableB: (() => void) | undefined;
  let markTableBStarted: (() => void) | undefined;
  const tableBRelease = new Promise<void>((resolve) => {
    releaseTableB = resolve;
  });
  const tableBStarted = new Promise<void>((resolve) => {
    markTableBStarted = resolve;
  });
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: tableNames.map((name) => ({
        name,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
      })),
      refreshed_at: "2026-07-21T00:00:00+00:00",
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      owner: "APP",
      items: tableNames.map((name) => ({
        name,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
      })),
      total: tableNames.length,
      table_count: tableNames.length,
      view_count: 0,
      next_cursor: null,
      refreshed_at: "2026-07-21T00:00:00+00:00",
      catalog_version: 1,
      warnings: [],
    }),
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
    const tableName = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1) ?? "");
    if (tableName === "TABLE_B") {
      markTableBStarted?.();
      await tableBRelease;
    }
    try {
      await fulfillJson(route, {
        name: tableName,
        owner: "APP",
        object_type: "table",
        row_count: 1,
        comment: "",
        columns: [
          {
            column_name: `${tableName}_ID`,
            logical_name: `${tableName} の識別子`,
            data_type: "NUMBER",
            nullable: false,
            comment: "",
            sample_values: ["1"],
          },
        ],
        ddl: "",
        warnings: [],
      });
    } catch {
      // TABLE_B は後続選択時に client 側で abort されるため fulfill 不可でも正常。
    }
  });

  await page.goto("/table-management");
  await expect(page.getByRole("heading", { name: "TABLE_A" })).toBeVisible();
  await page.getByRole("button", { name: "TABLE_B を表示" }).click();
  await tableBStarted;
  await page.getByRole("button", { name: "TABLE_A を表示" }).click();
  await expect(page.getByRole("heading", { name: "TABLE_A" })).toBeVisible();
  releaseTableB?.();
  await page.waitForTimeout(100);

  await expect(page.getByRole("heading", { name: "TABLE_B" })).toHaveCount(0);
  await expect(page.getByTestId("db-admin-detail-columns")).toContainText("TABLE_A の識別子");
});

test("Excel/CSV 取込フォームは取込方法を表示せずファイル選択を全幅で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );

  const importPanel = page.locator("#table-management-panel-import");
  await expect(importPanel).toBeVisible();
  const fileField = importPanel.getByTestId("table-import-file-field");
  await expect(fileField).toBeVisible();
  await expect(importPanel.getByText(/必須入力項目です。/)).toBeVisible();
  await expect(importPanel.locator('label[for="table-import-table-name"] span[aria-hidden="true"]')).toHaveText("*");
  await expect(importPanel.locator('label[for="table-import-sheet-name"] span[aria-hidden="true"]')).toHaveText("*");
  await expect(importPanel.getByTestId("table-import-file-field-input")).toHaveAttribute("aria-required", "true");
  await expect(importPanel.getByTestId("table-import-mode-field")).toHaveCount(0);
  await expect(importPanel.getByText("取込方法", { exact: true })).toHaveCount(0);
  await expect(importPanel.locator("select")).toHaveCount(0);

  const fileFieldBox = await fileField.boundingBox();
  const filePickerBox = await fileField.getByTestId("table-import-file-field-dropzone").boundingBox();
  const clearButtonBox = await fileField.getByRole("button", { name: "取込ファイルをクリア" }).boundingBox();
  const fillsAvailableWidth = await fileField.evaluate((element) => {
    const parent = element.parentElement;
    return Boolean(
      parent &&
        Math.abs(element.getBoundingClientRect().width - parent.getBoundingClientRect().width) <= 1
    );
  });

  expect(fileFieldBox).not.toBeNull();
  expect(filePickerBox).not.toBeNull();
  expect(clearButtonBox).not.toBeNull();
  expect(fillsAvailableWidth).toBe(true);
  expect(filePickerBox!.height).toBeGreaterThanOrEqual(44);
  expect(clearButtonBox!.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalScroll(page);
});

test("テーブル管理は一覧と作成・取込パネルを同じ外枠で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const listStyle = await topLevelPanelStyle(page, "list");
  expect(listStyle.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(listStyle.borderTopWidth).toBe("1px");
  expect(Number.parseFloat(listStyle.paddingTop)).toBeGreaterThan(0);

  // トップレベルはタブではなくツールバーのアクション。一覧が既定で、作成/取込は往復。
  for (const target of [
    { id: "create", buttonName: "テーブル作成" },
    { id: "import", buttonName: "Excel/CSV 取込(新規テーブル)" },
  ] as const) {
    await clickPageHeaderAction(page, "table-management-actions", target.buttonName);
    const panel = page.locator(`#table-management-panel-${target.id}`);
    await expect(panel).toBeVisible();
    expect(await topLevelPanelStyle(page, target.id)).toEqual(listStyle);

    const hasPageHorizontalScroll = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1
    );
    expect(hasPageHorizontalScroll).toBe(false);
    await page.getByRole("button", { name: "一覧に戻る" }).click();
  }
});

test("テーブル作成フォームの見出し・実行ボタン・ステップはExcel/CSV取込と同じ階層で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  await page.getByTestId("table-management-actions").getByRole("button", { name: "テーブル作成" }).click();
  const createPanel = page.locator("#table-management-panel-create");
  const createHeading = createPanel.getByRole("heading", { name: "テーブル作成", level: 2 });
  const createButton = createPanel.getByRole("button", { name: "SQL 実行" });
  const createSteps = createPanel.getByTestId("table-create-steps");
  await expect(createHeading).toBeVisible();
  await expect(createPanel.getByText("実行できるのは CREATE TABLE / COMMENT ON / DROP TABLE のみです。")).toBeVisible();
  await expect(createPanel.getByText("Oracle への SQL 実行")).toBeVisible();
  await expect(createButton).toBeDisabled();
  // 実行ボタンは実行確認語フィールドの直下(同一枠内)に配置する統一レイアウト
  await expect(createPanel.getByTestId("execution-confirmation-field").getByRole("button", { name: "SQL 実行" })).toBeVisible();
  await expect(createSteps.getByText("SQL 入力")).toBeVisible();
  await expect(createSteps.getByText("実行確認")).toBeVisible();

  const createStyle = await createHeading.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      tagName: node.tagName,
      display: computed.display,
      alignItems: computed.alignItems,
      gap: computed.gap,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      color: computed.color,
    };
  });
  const createButtonStyle = await compactVisualStyle(createButton);
  const createStepStyle = await compactVisualStyle(createSteps.locator("li").first());

  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );
  const importPanel = page.locator("#table-management-panel-import");
  const importHeading = importPanel.getByRole("heading", { name: "Excel/CSV 取込(新規テーブル)", level: 2 });
  const importButton = importPanel.getByRole("button", { name: "取込を実行" });
  const importSteps = importPanel.getByTestId("table-import-steps");
  await expect(importHeading).toBeVisible();
  await expect(importButton).toBeDisabled();
  await expect(importSteps.getByText("ファイル選択")).toBeVisible();
  await expect(importSteps.getByText("実行確認")).toBeVisible();

  const importStyle = await importHeading.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      tagName: node.tagName,
      display: computed.display,
      alignItems: computed.alignItems,
      gap: computed.gap,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      color: computed.color,
    };
  });
  const importButtonStyle = await compactVisualStyle(importButton);
  const importStepStyle = await compactVisualStyle(importSteps.locator("li").first());

  expect(createStyle).toEqual(importStyle);
  expect(createButtonStyle).toEqual(importButtonStyle);
  expect(createStepStyle).toEqual(importStepStyle);
});

test("テーブル管理はアクションボタンで作成・取込を開閉できる", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const actions = page.getByTestId("table-management-actions");
  await expect(page.locator("#table-management-panel-list")).toBeVisible();

  await actions.getByRole("button", { name: "テーブル作成" }).click();
  await expect(page.locator("#table-management-panel-create")).toBeVisible();
  await expect(page.locator("#table-management-panel-list")).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#table-management-panel-list")).toBeVisible();

  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );
  await expect(page.locator("#table-management-panel-import")).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#table-management-panel-list")).toBeVisible();
});

for (const scenario of metadataScenarios) {
  test(`${scenario.title}は共通カード枠と工程ステッパーで表示する`, async ({ page }) => {
    await mockMetadataManagementApi(page);
    await page.goto(scenario.path);

    const targetsStyle = await topLevelPanelStyle(page, "targets", scenario.idPrefix);
    expect(targetsStyle.backgroundColor).toBe("rgb(255, 255, 255)");
    expect(targetsStyle.borderTopWidth).toBe("1px");
    expect(Number.parseFloat(targetsStyle.paddingTop)).toBeGreaterThan(0);

    // タブではなく工程ステッパー。3 工程セクションは常時縦積みで同じカード枠を共有する。
    await expect(page.getByTestId(`${scenario.idPrefix}-steps`)).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(0);

    for (const id of ["targets", "input", "execute"] as const) {
      const panel = page.locator(`#${scenario.idPrefix}-panel-${id}`);
      await expect(panel).toBeVisible();
      expect(await topLevelPanelStyle(page, id, scenario.idPrefix)).toEqual(targetsStyle);
      if (id === "execute") {
        await expect(panel.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
        await expect(panel.getByLabel("Oracle に実行する")).toHaveCount(0);
        await expect(panel.getByRole("button", { name: "SQL 実行" })).toBeDisabled();
      }
    }

    const hasPageHorizontalScroll = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1
    );
    expect(hasPageHorizontalScroll).toBe(false);
  });

  test(`${scenario.title}は 150% zoom 相当でも工程・対象名・操作ボタンを折り返さない`, async ({ page }) => {
    await page.setViewportSize({ width: 1365, height: 900 });
    await mockMetadataManagementApi(page);
    await page.goto(scenario.path);

    await expect(page.getByTestId(`${scenario.idPrefix}-steps`)).toBeVisible();

    const grid = page.getByTestId(`${scenario.idPrefix}-target-grid`);
    await expect(grid.locator("tbody tr")).toHaveCount(30);
    await expectSingleLine(grid.getByRole("columnheader", { name: /種類/ }).locator("span").first());
    await expectSingleLine(grid.getByRole("columnheader", { name: /所有者/ }).locator("span").first());
    await expectSingleLine(grid.getByText("META_TABLE_01"));
    await expectSingleLine(page.getByRole("button", { name: "情報を取得" }).locator("span").first());

    const scroll = await page.getByTestId("db-admin-object-list").evaluate((node) => ({
      internalWidthStable: node.scrollWidth >= node.clientWidth,
      pageHorizontal:
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1,
    }));
    expect(scroll.internalWidthStable).toBe(true);
    expect(scroll.pageHorizontal).toBe(false);
  });
}

for (const scenario of prepareManagementScenarios) {
  test(`${scenario.title}はテーブル管理と同じタブ・パネル構造で表示する`, async ({ page }) => {
    await mockPrepareManagementApi(page, scenario.path);
    await page.goto(scenario.path);

    const first = scenario.tabs[0];
    const baseStyle = await topLevelPanelStyle(page, first.id, scenario.idPrefix);
    expect(baseStyle.backgroundColor).toBe("rgb(255, 255, 255)");
    expect(baseStyle.borderTopWidth).toBe("1px");
    expect(Number.parseFloat(baseStyle.paddingTop)).toBeGreaterThan(0);

    for (const target of scenario.tabs) {
      await page.getByRole("tab", { name: target.tabName }).click();
      const panel = page.locator(`#${scenario.idPrefix}-panel-${target.id}`);
      await expect(panel).toBeVisible();
      expect(await topLevelPanelStyle(page, target.id, scenario.idPrefix)).toEqual(baseStyle);
      await expectSingleLine(page.getByRole("tab", { name: target.tabName }).locator("span").last());
    }

    const hasPageHorizontalScroll = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1
    );
    expect(hasPageHorizontalScroll).toBe(false);
  });

  test(`${scenario.title}トップレベルタブはキーボードで切り替えられる`, async ({ page }) => {
    await mockPrepareManagementApi(page, scenario.path);
    await page.goto(scenario.path);

    const first = scenario.tabs[0]!;
    const second = scenario.tabs[1]!;
    const firstTab = page.getByRole("tab", { name: first.tabName });
    const secondTab = page.getByRole("tab", { name: second.tabName });

    await expect(firstTab).toHaveAttribute("aria-selected", "true");
    await firstTab.focus();
    await page.keyboard.press("ArrowRight");
    await expect(secondTab).toHaveAttribute("aria-selected", "true");
    await expect(page.locator(`#${scenario.idPrefix}-panel-${second.id}`)).toBeVisible();

    if (scenario.tabs.length > 2) {
      const third = scenario.tabs[2]!;
      const thirdTab = page.getByRole("tab", { name: third.tabName });
      await page.keyboard.press("ArrowRight");
      await expect(thirdTab).toHaveAttribute("aria-selected", "true");
      await expect(page.locator(`#${scenario.idPrefix}-panel-${third.id}`)).toBeVisible();
    }

    await page.keyboard.press("Home");
    await expect(firstTab).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: scenario.tabs[scenario.tabs.length - 1].tabName })).toHaveAttribute(
      "aria-selected",
      "true"
    );
  });

  test(`${scenario.title}は375px幅でもページ全体の横スクロールを出さない`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await mockPrepareManagementApi(page, scenario.path);
    await page.goto(scenario.path);

    await expect(page.getByRole("tab", { name: scenario.tabs[0].tabName })).toHaveAttribute("aria-selected", "true");
    const hasPageHorizontalScroll = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1
    );
    expect(hasPageHorizontalScroll).toBe(false);
  });
}

test("コメント管理の対象選択は空状態を表示する", async ({ page }) => {
  await mockMetadataManagementApi(page, { empty: true });
  await page.goto("/comment-management");

  await expect(page.getByTestId("comment-management-steps")).toBeVisible();
  await expect(page.getByText("対象がありません")).toBeVisible();
  await expect(page.getByText("DB 構造を再取得してから確認してください。")).toBeVisible();
});

test("ビュー管理は一覧と作成・JOIN/WHERE パネルを同じ外枠で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[1]);
  await page.goto("/view-management");

  const listStyle = await topLevelPanelStyle(page, "list", "view-management");
  expect(listStyle.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(listStyle.borderTopWidth).toBe("1px");
  expect(Number.parseFloat(listStyle.paddingTop)).toBeGreaterThan(0);

  for (const target of [
    { id: "create", buttonName: "ビュー作成" },
    { id: "joinWhere", buttonName: "JOIN/WHERE 条件抽出" },
  ] as const) {
    await clickPageHeaderAction(page, "view-management-actions", target.buttonName);
    const panel = page.locator(`#view-management-panel-${target.id}`);
    await expect(panel).toBeVisible();
    expect(await topLevelPanelStyle(page, target.id, "view-management")).toEqual(listStyle);
    await page.getByRole("button", { name: "一覧に戻る" }).click();
  }

  const grid = page.getByTestId("view-management-grid");
  await expect(grid.locator("tbody tr")).toHaveCount(30);
  const ownerHeader = grid.getByRole("columnheader", { name: /所有者/ });
  if (await ownerHeader.count()) {
    await expectSingleLine(ownerHeader.locator("span").first());
  }
  const rowAction = grid.getByRole("button", { name: /操作: APP\.VIEW_01/ });
  await expect(rowAction).toBeVisible();
  await expect(grid.getByRole("button", { name: "詳細" })).toHaveCount(0);
  await expect(grid.getByRole("button", { name: "削除" })).toHaveCount(0);
  await rowAction.click();
  const menu = page.getByRole("menu");
  await expect(menu).toHaveAttribute("data-floating-menu-placement", "bottom");
  await expect(page.getByRole("menuitem", { name: "詳細" })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: "削除" })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("ビュー管理はアクションボタンで作成・JOIN/WHERE を開閉できる", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[1]);
  await page.goto("/view-management");

  const actions = page.getByTestId("view-management-actions");
  await expect(page.locator("#view-management-panel-list")).toBeVisible();

  await actions.getByRole("button", { name: "ビュー作成" }).click();
  await expect(page.locator("#view-management-panel-create")).toBeVisible();
  await expect(page.locator("#view-management-panel-list")).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#view-management-panel-list")).toBeVisible();

  await clickPageHeaderAction(page, "view-management-actions", "JOIN/WHERE 条件抽出");
  await expect(page.locator("#view-management-panel-joinWhere")).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#view-management-panel-list")).toBeVisible();
});

test("Excel/CSV 取込の実行確認語は ADMIN_EXECUTE 固定で判定する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  await clickPageHeaderAction(
    page,
    "table-management-actions",
    "Excel/CSV 取込(新規テーブル)"
  );

  const importPanel = page.locator("#table-management-panel-import");
  const confirmationField = importPanel.getByTestId("execution-confirmation-field");
  const executeButton = importPanel.getByRole("button", { name: "取込を実行" });
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();
  // 実行ボタンは実行確認語フィールドの直下(同一枠内)に配置する統一レイアウト
  await expect(confirmationField.getByRole("button", { name: "取込を実行" })).toBeVisible();

  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(importPanel.getByText("確認済み", { exact: true })).toHaveCount(1);
  await expect(executeButton).toBeDisabled();

  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  await importPanel.getByLabel("実行確認語").fill("IMPORTED_ORDERS");
  await expect(confirmationField.getByText("不一致")).toBeVisible();
  await expect(executeButton).toBeDisabled();
});
