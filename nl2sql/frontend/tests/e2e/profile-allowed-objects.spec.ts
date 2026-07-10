import { expect, test, type Page, type Route } from "@playwright/test";

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const selectAiConfig = {
  profile_name: "NL2SQL_DEFAULT_PROFILE",
  region: "ap-osaka-1",
  model: "cohere.command-r-plus",
  embedding_model: "cohere.embed-v4.0",
  max_tokens: 32000,
  enforce_object_list: true,
  comments: true,
  annotations: false,
  constraints: false,
};

const schemaCatalog = {
  refreshed_at: "2026-06-21T10:00:00.000Z",
  tables: [
    ...Array.from({ length: 32 }, (_, index) => {
      const count = String(index + 1).padStart(2, "0");
      return {
        table_name: `TABLE_${count}`,
        logical_name: `表論理名_${count}`,
        owner: "APP",
        table_type: "TABLE",
        comment: `表コメント_${count}`,
        row_count: null,
        columns: [],
        constraints: [],
      };
    }),
    ...Array.from({ length: 32 }, (_, index) => {
      const count = String(index + 1).padStart(2, "0");
      return {
        table_name: `VIEW_${count}`,
        logical_name: `ビュー論理名_${count}`,
        owner: "APP",
        table_type: "VIEW",
        comment: `ビューコメント_${count}`,
        row_count: null,
        columns: [],
        constraints: [],
      };
    }),
    {
      table_name: "SYS$AUDIT",
      logical_name: "システム監査",
      owner: "SYS",
      table_type: "TABLE",
      comment: "system table",
      row_count: null,
      columns: [],
      constraints: [],
    },
    {
      table_name: "V_$SESSION",
      logical_name: "システムセッション",
      owner: "SYS",
      table_type: "VIEW",
      comment: "system view",
      row_count: null,
      columns: [],
      constraints: [],
    },
  ],
};

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定プロファイル",
    description: "許可オブジェクトの表示確認",
    allowed_tables: ["TABLE_01"],
    allowed_views: ["VIEW_02"],
    glossary: {},
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: selectAiConfig,
    archived: false,
  },
];

const dbProfiles = {
  runtime: "deterministic",
  profiles: [
    {
      name: "NL2SQL_DEFAULT_PROFILE",
      status: "dry_run",
      owner: "APP",
      created_at: "2026-06-21T10:00:00.000Z",
      description: "既定プロファイル",
      category: "既定プロファイル",
      object_list: [
        { owner: "APP", name: "TABLE_01" },
        { owner: "APP", name: "VIEW_02" },
      ],
      tables: ["TABLE_01"],
      views: ["VIEW_02"],
      region: "ap-osaka-1",
      model: "cohere.command-r-plus",
      embedding_model: "cohere.embed-v4.0",
      schema_text: "",
      context_ddl: "",
      attributes: {
        provider: "oci",
        region: "ap-osaka-1",
        model: "cohere.command-r-plus",
        embedding_model: "cohere.embed-v4.0",
        object_list: [
          { owner: "APP", name: "TABLE_01" },
          { owner: "APP", name: "VIEW_02" },
        ],
      },
    },
  ],
  warnings: [],
};

async function mockProfileApi(page: Page) {
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: [
        { name: "VIEW_02", owner: "APP", object_type: "VIEW", row_count: null, comment: "view" },
        { name: "V_$SESSION", owner: "SYS", object_type: "VIEW", row_count: null, comment: "system" },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles?include_detail=true", (route) => fulfillJson(route, dbProfiles));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
}

test("業務プロファイルは表とビューを固定高リストで管理できる", async ({ page }) => {
  let savedPayload: Record<string, unknown> | null = null;
  await mockProfileApi(page);
  await page.route("**/api/nl2sql/profiles/default", async (route) => {
    savedPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, { ...profiles[0], ...savedPayload, id: "default" });
  });

  await page.goto("/profiles");

  const listTab = page.getByRole("tab", { name: "一覧と詳細" });
  const createTab = page.getByRole("tab", { name: "新規作成" });
  const oracleTab = page.getByRole("tab", { name: "Oracle Profile" });
  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await listTab.press("ArrowRight");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await createTab.press("ArrowRight");
  await expect(oracleTab).toHaveAttribute("aria-selected", "true");
  await oracleTab.press("ArrowLeft");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await listTab.click();

  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");
  await expect(tableList.getByText("TABLE_01", { exact: true })).toBeVisible();
  await expect(viewList.getByText("VIEW_02", { exact: true })).toBeVisible();
  await expect(tableList.getByText("VIEW_02", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("TABLE_01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("SYS$AUDIT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("V_$SESSION", { exact: true })).toHaveCount(0);
  await expect(page.getByText("表論理名_01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("ビューコメント_02", { exact: true })).toHaveCount(0);

  const fit = await tableList.evaluate((node) => {
    const listBox = node.getBoundingClientRect();
    const rows = Array.from(node.querySelectorAll("label")).map((row) => row.getBoundingClientRect());
    const visibleRows = rows.filter((row) => row.bottom <= listBox.bottom + 1 && row.top >= listBox.top - 1).length;
    return {
      listHeight: listBox.height,
      visibleRows,
      totalRows: rows.length,
      scrollable: node.scrollHeight > node.clientHeight,
      noHorizontalOverflow: node.scrollWidth <= node.clientWidth + 1,
    };
  });
  expect(fit.listHeight).toBeGreaterThanOrEqual(388);
  expect(fit.listHeight).toBeLessThanOrEqual(396);
  expect(fit.visibleRows).toBeGreaterThan(0);
  expect(fit.visibleRows).toBeLessThan(fit.totalRows);
  expect(fit.scrollable).toBe(true);
  expect(fit.noHorizontalOverflow).toBe(true);

  await tableList.getByLabel("TABLE_03").check();
  await viewList.getByLabel("VIEW_04").check();
  await page.getByRole("button", { name: "保存" }).click();
  const payload = savedPayload as {
    allowed_tables: string[];
    allowed_views: string[];
    select_ai_config: Record<string, unknown>;
  } | null;
  expect(payload?.allowed_tables).toEqual(["TABLE_01", "TABLE_03"]);
  expect(payload?.allowed_views).toEqual(["VIEW_02", "VIEW_04"]);
  expect(payload?.select_ai_config).toMatchObject({
    embedding_model: "cohere.embed-v4.0",
    enforce_object_list: true,
  });

  const bodyWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
});

test("Oracle Profile タブで JSON と SQL preview と drop 確認を扱える", async ({ page }) => {
  let dropPayload: Record<string, unknown> | null = null;
  await mockProfileApi(page);
  await page.route("**/api/nl2sql/select-ai/db-profiles/NL2SQL_DEFAULT_PROFILE/drop", async (route) => {
    dropPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, {
      engine: "select_ai",
      executed: true,
      status: "dropped",
      cleaned_at: "2026-06-21T10:00:00.000Z",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      team_name: "",
      warning: "",
      asset_names: {},
      engine_meta: {},
    });
  });

  await page.goto("/profiles");
  await page.getByRole("tab", { name: "Oracle Profile" }).click();

  await expect(page.getByTestId("profile-oracle-list").getByText("NL2SQL_DEFAULT_PROFILE")).toBeVisible();
  await expect(page.getByText("cohere.embed-v4.0").first()).toBeVisible();
  await expect(page.getByText("DBMS_CLOUD_AI.CREATE_PROFILE")).toBeVisible();
  await expect(page.getByLabel("Attributes JSON")).toHaveValue(/"provider": "oci"/);

  await page.getByRole("button", { name: "Drop 実行" }).click();
  const dialog = page.getByRole("dialog", { name: "Oracle Profile Drop の確認" });
  const executeButton = dialog.getByRole("button", { name: "Drop 実行" });
  await expect(executeButton).toBeDisabled();
  await dialog.getByLabel("実行確認語").fill("NL2SQL_DEFAULT_PROFILE");
  await expect(executeButton).toBeEnabled();
  await executeButton.click();
  expect(dropPayload).toMatchObject({
    execute: true,
    confirmation: "NL2SQL_DEFAULT_PROFILE",
  });
});
