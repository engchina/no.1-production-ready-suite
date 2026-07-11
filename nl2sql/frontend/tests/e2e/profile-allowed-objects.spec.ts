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
  role: "既定の Oracle SQL アシスタント",
  additional_instructions: "金額は円単位で表示する。",
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
      status: "ready",
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
        role: "既定の Oracle SQL アシスタント",
        additional_instructions: "金額は円単位で表示する。",
        object_list: [
          { owner: "APP", name: "TABLE_01" },
          { owner: "APP", name: "VIEW_02" },
        ],
      },
    },
  ],
  warnings: [],
};

const longOracleProfileName =
  "DTAIPR_1171E8D8A630_LONG_ORACLE_PROFILE_NAME_THAT_SHOULD_WRAP_INSIDE_THE_LEFT_COLUMN";

const longNameDbProfiles = {
  ...dbProfiles,
  profiles: [
    {
      ...dbProfiles.profiles[0],
      name: longOracleProfileName,
      description: "長い Oracle Profile 名の折り返し確認",
    },
  ],
};

const unfilteredDbProfiles = {
  ...dbProfiles,
  profiles: [
    ...dbProfiles.profiles,
    {
      name: "MANUAL_SELECT_AI",
      status: "ready",
      owner: "APP",
      created_at: "2026-06-21T10:00:00.000Z",
      description: "業務プロファイル外で作成された profile",
      category: "manual",
      object_list: [{ owner: "APP", name: "TABLE_99" }],
      tables: ["TABLE_99"],
      views: [],
      region: "ap-osaka-1",
      model: "cohere.command-r-plus",
      embedding_model: "cohere.embed-v4.0",
      schema_text: "",
      context_ddl: "",
      attributes: {
        provider: "oci",
        object_list: [{ owner: "APP", name: "TABLE_99" }],
      },
    },
  ],
};

async function mockProfileApi(
  page: Page,
  options: {
    catalog?: typeof schemaCatalog;
    viewItems?: Array<{ name: string; owner: string; object_type: string; row_count: null; comment: string }>;
    profileItems?: typeof profiles;
    dbProfileData?: typeof dbProfiles;
  } = {}
) {
  const viewItems = options.viewItems ?? [
    { name: "VIEW_02", owner: "APP", object_type: "VIEW", row_count: null, comment: "view" },
    { name: "V_$SESSION", owner: "SYS", object_type: "VIEW", row_count: null, comment: "system" },
  ];
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, options.catalog ?? schemaCatalog));
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items: viewItems,
      warnings: [],
    })
  );
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?include_detail=true&business_profiles_only=true&include_archived_business_profiles=true",
    (route) => fulfillJson(route, options.dbProfileData ?? dbProfiles)
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles?include_detail=true", (route) =>
    fulfillJson(route, unfilteredDbProfiles)
  );
  await page.route("**/api/nl2sql/profiles?include_archived=true", (route) => fulfillJson(route, options.profileItems ?? profiles));
}

test("業務プロファイルは表とビューを固定高リストで管理できる", async ({ page }) => {
  let savedPayload: Record<string, unknown> | null = null;
  let oraclePayload: Record<string, unknown> | null = null;
  await mockProfileApi(page);
  await page.route("**/api/nl2sql/profiles/default**", async (route) => {
    savedPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, { ...profiles[0], ...savedPayload, id: "default" });
  });
  await page.route("**/api/nl2sql/profiles/default/select-ai-profile", async (route) => {
    oraclePayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, {
      runtime: "oracle",
      executed: true,
      status: "saved",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      original_name: "",
      ddl: ["BEGIN DBMS_CLOUD_AI.CREATE_PROFILE(profile_name => :name, attributes => :attrs); END;"],
      profile: dbProfiles.profiles[0],
      warnings: [],
      engine_meta: {},
    });
  });

  await page.goto("/profiles");

  await expect(page.getByRole("button", { name: "新規", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "再読込", exact: true })).toHaveCount(1);

  const listTab = page.getByRole("tab", { name: "一覧と詳細" });
  const createTab = page.getByRole("tab", { name: "新規作成" });
  const oracleTab = page.getByRole("tab", { name: "Oracle Profile" });
  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await listTab.press("ArrowRight");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "新規プロファイル" })).toBeVisible();
  await createTab.press("ArrowRight");
  await expect(oracleTab).toHaveAttribute("aria-selected", "true");
  await oracleTab.press("ArrowLeft");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await listTab.click();
  await page.getByRole("button", { name: /既定プロファイル 許可オブジェクトの表示確認/ }).click();

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

  const objectSection = page.getByTestId("profile-allowed-object-list");
  const objectSearchToolbar = page.getByTestId("profile-object-search-toolbar");
  const objectSearch = page.getByRole("searchbox", { name: "オブジェクト検索" });
  await expect(objectSearch).toHaveAttribute("placeholder", "表・ビュー名で検索");
  await expect(objectSearchToolbar.locator("svg.lucide-search")).toBeVisible();

  const [headingBox, toolbarBox, listsBox, searchBox] = await Promise.all([
    objectSection.getByText("対象オブジェクト", { exact: true }).boundingBox(),
    objectSearchToolbar.boundingBox(),
    tableList.boundingBox(),
    objectSearch.boundingBox(),
  ]);
  expect(headingBox).not.toBeNull();
  expect(toolbarBox).not.toBeNull();
  expect(listsBox).not.toBeNull();
  expect(searchBox).not.toBeNull();
  expect(toolbarBox!.y).toBeGreaterThan(headingBox!.y);
  expect(listsBox!.y).toBeGreaterThan(toolbarBox!.y + toolbarBox!.height - 1);
  expect(searchBox!.x).toBeGreaterThanOrEqual(toolbarBox!.x + 11);
  expect(searchBox!.x).toBeLessThanOrEqual(toolbarBox!.x + 14);

  await expect(tableList.getByLabel("TABLE_01")).toBeChecked();
  await expect(viewList.getByLabel("VIEW_02")).toBeChecked();
  await objectSearch.fill("03");
  await expect(tableList.getByText("TABLE_03", { exact: true })).toBeVisible();
  await expect(viewList.getByText("VIEW_03", { exact: true })).toBeVisible();
  await expect(tableList.getByText("TABLE_01", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("VIEW_02", { exact: true })).toHaveCount(0);
  await objectSearch.clear();
  await expect(tableList.getByLabel("TABLE_01")).toBeChecked();
  await expect(viewList.getByLabel("VIEW_02")).toBeChecked();

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
  const roleField = page.getByLabel("アシスタントロール");
  const instructionsField = page.getByLabel("追加指示", { exact: true });
  const roleBox = await roleField.boundingBox();
  const instructionsBox = await instructionsField.boundingBox();
  expect(roleBox?.height).toBe(instructionsBox?.height);
  await roleField.fill("財務分析向け Oracle SQL アシスタント");
  await instructionsField.fill("日付は DATE 型で返す。");
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
    role: "財務分析向け Oracle SQL アシスタント",
    additional_instructions: "日付は DATE 型で返す。",
  });

  await page.getByLabel("実行確認語").last().fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "Oracle Profile 反映" }).click();
  expect(oraclePayload).toMatchObject({
    confirmation: "ADMIN_EXECUTE",
    reason: "ui-profile-management-select-ai-upsert",
  });
  expect(oraclePayload).not.toHaveProperty("execute");
  await expect(page.getByTestId("profile-oracle-result").getByText("saved")).toBeVisible();

  await page.setViewportSize({ width: 375, height: 900 });
  const mobileRoleBox = await roleField.boundingBox();
  const mobileInstructionsBox = await instructionsField.boundingBox();
  expect(mobileRoleBox?.height).toBe(mobileInstructionsBox?.height);
  const [mobileToolbarBox, mobileSearchBox] = await Promise.all([
    objectSearchToolbar.boundingBox(),
    objectSearch.boundingBox(),
  ]);
  expect(mobileToolbarBox).not.toBeNull();
  expect(mobileSearchBox).not.toBeNull();
  expect(mobileSearchBox!.width).toBeGreaterThanOrEqual(mobileToolbarBox!.width - 26);

  const bodyWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
});

test("業務プロファイルの対象オブジェクト空状態はExcelプレビュー風の広い面で表示する", async ({ page }) => {
  await mockProfileApi(page, {
    catalog: { ...schemaCatalog, tables: [] },
    viewItems: [],
  });

  await page.goto("/profiles");
  await page.getByRole("button", { name: /既定プロファイル 許可オブジェクトの表示確認/ }).click();

  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");

  await expect(tableList).toHaveAttribute("aria-label", "テーブル選択");
  await expect(viewList).toHaveAttribute("aria-label", "ビュー選択");
  await expect(tableList.getByText("選択できるテーブルがありません。")).toBeVisible();
  await expect(tableList.getByText("Oracle からテーブルを読み込むとここに表示されます。")).toBeVisible();
  await expect(viewList.getByText("選択できるビューがありません。")).toBeVisible();
  await expect(viewList.getByText("Oracle からビューを読み込むとここに表示されます。")).toBeVisible();
  await expect(tableList.locator("label")).toHaveCount(0);
  await expect(viewList.locator("label")).toHaveCount(0);

  const surface = await tableList.evaluate((node) => {
    const style = window.getComputedStyle(node);
    return {
      height: node.getBoundingClientRect().height,
      borderStyle: style.borderStyle,
      backgroundColor: style.backgroundColor,
      dashedDescendants: node.querySelectorAll(".border-dashed").length,
      noHorizontalOverflow: node.scrollWidth <= node.clientWidth + 1,
    };
  });
  expect(surface.height).toBeGreaterThanOrEqual(388);
  expect(surface.height).toBeLessThanOrEqual(396);
  expect(surface.borderStyle).toBe("solid");
  expect(surface.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(surface.dashedDescendants).toBe(0);
  expect(surface.noHorizontalOverflow).toBe(true);

  await page.setViewportSize({ width: 375, height: 900 });
  const mobileWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(mobileWidth.scrollWidth).toBeLessThanOrEqual(mobileWidth.clientWidth + 1);
});

test("Oracle Profile 一覧は長い profile 名を折り返し操作ボタンを横並びに保つ", async ({ page }) => {
  await mockProfileApi(page, { dbProfileData: longNameDbProfiles });

  await page.goto("/profiles");
  await page.getByRole("tab", { name: "Oracle Profile" }).click();

  const oracleList = page.getByTestId("profile-oracle-list");
  await expect(oracleList.getByText(longOracleProfileName)).toBeVisible();

  const row = oracleList.getByRole("row").filter({ hasText: longOracleProfileName });
  await expect(row.getByRole("button", { name: "詳細" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Drop 実行" })).toBeVisible();

  const metrics = await row.evaluate((rowNode) => {
    const cells = Array.from(rowNode.querySelectorAll("td"));
    const profileName = cells[0]?.querySelector("button > span");
    const buttons = Array.from(cells[2]?.querySelectorAll("button") ?? []);
    if (!cells[0] || !cells[2] || !profileName || buttons.length < 2) {
      throw new Error("Oracle Profile list row was not rendered as expected.");
    }

    const nameCellRect = cells[0].getBoundingClientRect();
    const actionCellRect = cells[2].getBoundingClientRect();
    const profileNameRect = profileName.getBoundingClientRect();
    const profileNameStyle = window.getComputedStyle(profileName);
    const detailRect = buttons[0].getBoundingClientRect();
    const dropRect = buttons[1].getBoundingClientRect();

    return {
      actionCellWidth: actionCellRect.width,
      buttonBottomDelta: Math.abs(detailRect.bottom - dropRect.bottom),
      buttonTopDelta: Math.abs(detailRect.top - dropRect.top),
      detailRight: detailRect.right,
      dropLeft: dropRect.left,
      nameCellWidth: nameCellRect.width,
      profileNameHeight: profileNameRect.height,
      profileNameLineHeight: Number.parseFloat(profileNameStyle.lineHeight),
    };
  });

  expect(metrics.profileNameHeight).toBeGreaterThan(metrics.profileNameLineHeight * 1.5);
  expect(metrics.nameCellWidth).toBeLessThan(metrics.actionCellWidth);
  expect(metrics.buttonTopDelta).toBeLessThan(2);
  expect(metrics.buttonBottomDelta).toBeLessThan(2);
  expect(metrics.detailRight).toBeLessThan(metrics.dropLeft);

  const bodyWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
});

test("Oracle Profile タブで JSON と SQL preview と drop 確認を扱える", async ({ page }) => {
  let dropPayload: Record<string, unknown> | null = null;
  let savePayload: Record<string, unknown> | null = null;
  await mockProfileApi(page);
  await page.route("**/api/nl2sql/select-ai/db-profiles", async (route) => {
    savePayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, {
      runtime: "oracle",
      executed: true,
      status: "saved",
      profile_name: "NL2SQL_DEFAULT_PROFILE",
      original_name: "NL2SQL_DEFAULT_PROFILE",
      ddl: [],
      profile: dbProfiles.profiles[0],
      warnings: [],
      engine_meta: {},
    });
  });
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
  await expect(page.getByTestId("profile-oracle-list").getByText("MANUAL_SELECT_AI")).toHaveCount(0);
  await expect(page.getByText("cohere.embed-v4.0").first()).toBeVisible();
  await expect(page.getByText("DBMS_CLOUD_AI.CREATE_PROFILE")).toBeVisible();
  await expect(page.getByLabel("Attributes JSON")).toHaveValue(/"provider": "oci"/);

  await page.getByRole("button", { name: "保存実行" }).click();
  const saveDialog = page.getByRole("alertdialog", { name: "Oracle Profile 保存の確認" });
  await expect(saveDialog).toBeVisible();
  await saveDialog.getByRole("button", { name: "保存実行" }).click();
  expect(savePayload).toMatchObject({
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    confirmation: "NL2SQL_DEFAULT_PROFILE",
  });
  expect(savePayload).not.toHaveProperty("execute");

  await page.getByRole("button", { name: "Drop 実行" }).click();
  const dialog = page.getByRole("dialog", { name: "Oracle Profile Drop の確認" });
  const executeButton = dialog.getByRole("button", { name: "Drop 実行" });
  await expect(executeButton).toBeDisabled();
  await dialog.getByLabel("実行確認語").fill("NL2SQL_DEFAULT_PROFILE");
  await expect(executeButton).toBeEnabled();
  await executeButton.click();
  expect(dropPayload).toMatchObject({
    confirmation: "NL2SQL_DEFAULT_PROFILE",
  });
  expect(dropPayload).not.toHaveProperty("execute");
});
