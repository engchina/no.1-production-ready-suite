import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
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
      { id: "csv", tabName: "CSV アップロード(既存テーブル)" },
      { id: "sql", tabName: "SQL 一括実行" },
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

async function mockObjectManagementApi(page: Page, scenario: (typeof scenarios)[number]) {
  const items = Array.from({ length: 30 }, (_, index) => ({
    name: `${scenario.prefix}_${String(index + 1).padStart(2, "0")}`,
    owner: "APP",
    object_type: scenario.objectType,
    row_count: null,
    comment: "",
  }));

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route(scenario.apiPath, (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) =>
    fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      row_count: null,
      comment: "",
      columns: [],
      ddl: 'CREATE TABLE "TABLE_01" ("ID" NUMBER)',
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/views/*", (route) =>
    fulfillJson(route, {
      name: "VIEW_01",
      owner: "APP",
      object_type: "view",
      row_count: null,
      comment: "",
      columns: [],
      ddl: 'CREATE OR REPLACE VIEW "VIEW_01" AS SELECT 1 AS ID FROM DUAL',
      warnings: [],
    })
  );
}

async function mockMetadataManagementApi(page: Page, options: { empty?: boolean } = {}) {
  const tableItems = options.empty
    ? []
    : Array.from({ length: 20 }, (_, index) => ({
        name: `META_TABLE_${String(index + 1).padStart(2, "0")}`,
        owner: "APP",
        object_type: "table",
        row_count: index,
        comment: `コメント対象テーブル ${index + 1}`,
      }));
  const viewItems = options.empty
    ? []
    : Array.from({ length: 10 }, (_, index) => ({
        name: `META_VIEW_${String(index + 1).padStart(2, "0")}`,
        owner: "APP",
        object_type: "view",
        row_count: null,
        comment: `コメント対象ビュー ${index + 1}`,
      }));
  const catalog = {
    refreshed_at: "2026-06-21T10:00:00.000Z",
    tables: [],
  };

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, catalog));
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
}

async function mockDataManagementApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/schema/refresh", (route) => fulfillJson(route, managementCatalog));
  await page.route("**/api/nl2sql/db-admin/tables", (route) => fulfillJson(route, tableObjects));
  await page.route("**/api/nl2sql/db-admin/views", (route) => fulfillJson(route, viewObjects));
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [selectAiProfile], warnings: [] })
  );
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
    `${scenario.title}はグリッドの高さを上限内に収める`,
    async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    await expect(page.getByTestId(`${scenario.objectType}-management-grid`).locator("tbody tr")).toHaveCount(30);

    const fit = await list.evaluate((node) => {
      const listBox = node.getBoundingClientRect();
      const rowSelector = node.querySelector("tbody tr") ? "tbody tr" : "button";
      const rows = Array.from(node.querySelectorAll(rowSelector)).map((row) => row.getBoundingClientRect());
      return {
        listHeight: listBox.height,
        firstInside: rows[0].top >= listBox.top - 1 && rows[0].bottom <= listBox.bottom + 1,
        tenthInside: rows[9].bottom <= listBox.bottom + 1,
        eleventhBelow: rows[10].bottom > listBox.bottom + 1,
        lastBelow: rows[rows.length - 1].bottom > listBox.bottom + 1,
      };
    });

    expect(fit.listHeight).toBeGreaterThanOrEqual(320);
    expect(fit.listHeight).toBeLessThanOrEqual(680);
    expect(fit.firstInside).toBe(true);
    expect(fit.lastBelow).toBe(true);
    }
  );

  test(`${scenario.title}は共通検索フィールドで絞り込める`, async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const search = page.getByRole("searchbox", { name: "検索" });
    await expect(search).toHaveAttribute("placeholder", "名前・コメントで絞り込み");
    await expect(search.locator("xpath=ancestor::label").locator("svg.lucide-search")).toBeVisible();

    const grid = page.getByTestId(`${scenario.objectType}-management-grid`);
    await search.fill("_01");
    await expect(grid.locator("tbody tr")).toHaveCount(1);
    await expect(grid.getByText(`${scenario.prefix}_01`, { exact: true })).toBeVisible();
    await search.clear();
    await expect(grid.locator("tbody tr")).toHaveCount(30);
  });
}

for (const scenario of metadataScenarios) {
  test(`${scenario.title}は対象グリッドの高さを上限内に収める`, async ({ page }) => {
    await mockMetadataManagementApi(page);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    await expect(page.getByTestId(`${scenario.idPrefix}-target-grid`).locator("tbody tr")).toHaveCount(30);

    const fit = await list.evaluate((node) => {
      const listBox = node.getBoundingClientRect();
      const rows = Array.from(node.querySelectorAll("tbody tr")).map((row) => row.getBoundingClientRect());
      return {
        listHeight: listBox.height,
        firstInside: rows[0].top >= listBox.top - 1 && rows[0].bottom <= listBox.bottom + 1,
        tenthInside: rows[9].bottom <= listBox.bottom + 1,
        eleventhBelow: rows[10].bottom > listBox.bottom + 1,
        lastBelow: rows[rows.length - 1].bottom > listBox.bottom + 1,
      };
    });

    expect(fit.listHeight).toBeGreaterThanOrEqual(320);
    expect(fit.listHeight).toBeLessThanOrEqual(680);
    expect(fit.firstInside).toBe(true);
    expect(fit.tenthInside).toBe(true);
    expect(fit.eleventhBelow).toBe(true);
    expect(fit.lastBelow).toBe(true);
  });
}

test("テーブル管理は 150% zoom 相当でもタブ・列名・操作ボタンを折り返さない", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  // 一覧が既定。作成/取込はタブではなくツールバーのアクションボタン。
  const actions = page.getByTestId("table-management-actions");
  await expectSingleLine(actions.getByRole("button", { name: "テーブル作成" }).locator("span"));
  await expectSingleLine(actions.getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).locator("span"));
  // 詳細内タブ(列情報/DDL)は維持。
  await expectSingleLine(page.getByRole("tab", { name: "列情報" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "DDL" }).locator("span").last());

  const grid = page.getByTestId("table-management-grid");
  await expect(grid.locator("tbody tr")).toHaveCount(30);
  await expect(grid.getByRole("columnheader", { name: /コメント/ })).toHaveCount(0);
  await expectSingleLine(grid.getByRole("columnheader", { name: /所有者/ }).locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "詳細" }).first().locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "削除" }).first().locator("span").first());

  const scroll = await page.getByTestId("db-admin-object-list").evaluate((node) => ({
    internalWidthStable: node.scrollWidth >= node.clientWidth,
    pageHorizontal:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1,
  }));
  expect(scroll.internalWidthStable).toBe(true);
  expect(scroll.pageHorizontal).toBe(false);
});

test("テーブル詳細は列タブでは DDL を取得せず、DDL タブ初回表示で後追い取得する", async ({
  page,
}) => {
  const detailUrls: string[] = [];
  let catalogHit = false;
  await page.route("**/api/schema/catalog", (route) => {
    catalogHit = true;
    return fulfillJson(route, { refreshed_at: "2026-06-21T10:00:00.000Z", tables: [] });
  });
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        { name: "TABLE_01", owner: "APP", object_type: "table", row_count: 7, comment: "" },
      ],
      refreshed_at: "2026-07-14T03:53:00+00:00",
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) => {
    const url = route.request().url();
    detailUrls.push(url);
    // 重い GET_DDL を伴う DDL は include_ddl=1 のときだけ返す(バックエンドの挙動を模す)。
    const withDdl = url.includes("include_ddl=1");
    return fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      // 既定は num_rows 統計、exact_count=1 のときだけ COUNT(*) 相当の正確値を返す。
      row_count: url.includes("exact_count=1") ? 999 : 10,
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
  await expect(page.getByText("10 行")).toBeVisible(); // 既定は num_rows 統計
  expect(detailUrls.length).toBeGreaterThan(0);
  expect(detailUrls.every((url) => url.includes("include_ddl=0"))).toBe(true);
  expect(detailUrls.some((url) => url.includes("include_ddl=1"))).toBe(false);
  expect(detailUrls.some((url) => url.includes("exact_count=1"))).toBe(false);
  // catalog 全取得は行わない(サンプル値も取得日時も一覧/詳細で賄う)。
  expect(catalogHit).toBe(false);

  // 「正確な件数を取得」で exact_count=1 を明示取得し、行数バッジが更新される。
  await page.getByRole("button", { name: "COUNT(*) で正確な行数を取得" }).click();
  await expect(page.getByText("999 行")).toBeVisible();
  expect(detailUrls.some((url) => url.includes("exact_count=1"))).toBe(true);

  // DDL タブを開くと include_ddl=1 で後追い取得し、DDL が表示される。
  await page.getByRole("tab", { name: "DDL" }).click();
  await expect(page.getByText('CREATE TABLE "TABLE_01" ("ID" NUMBER)')).toBeVisible();
  expect(detailUrls.some((url) => url.includes("include_ddl=1"))).toBe(true);
});

test("Excel/CSV 取込フォームはファイル選択と取込方法の高さを揃える", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  await page
    .getByTestId("table-management-actions")
    .getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" })
    .click();

  const importPanel = page.locator("#table-management-panel-import");
  await expect(importPanel).toBeVisible();
  const fileField = importPanel.getByTestId("table-import-file-field");
  const modeField = importPanel.getByTestId("table-import-mode-field");
  await expect(fileField).toBeVisible();
  await expect(modeField).toBeVisible();
  await expect(modeField.getByText("取込方法")).toBeVisible();
  await expect(modeField.locator("option")).toHaveText([
    "新規作成",
    "既存データに追加",
    "データを全削除して取込",
    "テーブルを再作成",
  ]);

  const fileFieldBox = await fileField.boundingBox();
  const modeFieldBox = await modeField.boundingBox();
  const filePickerBox = await fileField.locator("label").first().boundingBox();
  const modeSelectBox = await modeField.locator("select").boundingBox();
  const clearButtonBox = await fileField.getByRole("button", { name: "取込ファイルをクリア" }).boundingBox();

  expect(fileFieldBox).not.toBeNull();
  expect(modeFieldBox).not.toBeNull();
  expect(filePickerBox).not.toBeNull();
  expect(modeSelectBox).not.toBeNull();
  expect(clearButtonBox).not.toBeNull();
  expect(Math.abs(fileFieldBox!.height - modeFieldBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(filePickerBox!.height - modeSelectBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(clearButtonBox!.height - modeSelectBox!.height)).toBeLessThanOrEqual(1);
});

test("テーブル管理は一覧と作成・取込パネルを同じ外枠で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const listStyle = await topLevelPanelStyle(page, "list");
  expect(listStyle.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(listStyle.borderTopWidth).toBe("1px");
  expect(Number.parseFloat(listStyle.paddingTop)).toBeGreaterThan(0);

  // トップレベルはタブではなくツールバーのアクション。一覧が既定で、作成/取込は往復。
  const actions = page.getByTestId("table-management-actions");
  for (const target of [
    { id: "create", buttonName: "テーブル作成" },
    { id: "import", buttonName: "Excel/CSV 取込(新規テーブル)" },
  ] as const) {
    await actions.getByRole("button", { name: target.buttonName }).click();
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
  await page
    .getByTestId("table-management-actions")
    .getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" })
    .click();
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

  await actions.getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).click();
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

  const actions = page.getByTestId("view-management-actions");
  for (const target of [
    { id: "create", buttonName: "ビュー作成" },
    { id: "joinWhere", buttonName: "JOIN/WHERE 条件抽出" },
  ] as const) {
    await actions.getByRole("button", { name: target.buttonName }).click();
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
  await expectSingleLine(grid.getByRole("button", { name: "詳細" }).first().locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "削除" }).first().locator("span").first());
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

  await actions.getByRole("button", { name: "JOIN/WHERE 条件抽出" }).click();
  await expect(page.locator("#view-management-panel-joinWhere")).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る" }).click();
  await expect(page.locator("#view-management-panel-list")).toBeVisible();
});

test("Excel/CSV 取込の実行確認語は ADMIN_EXECUTE 固定で判定する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  await page.getByTestId("table-management-actions").getByRole("button", { name: "Excel/CSV 取込(新規テーブル)" }).click();

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
