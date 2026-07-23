import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

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
    allowed_tables: ["APP.TABLE_01"],
    allowed_views: ["APP.VIEW_02"],
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
      tables: ["APP.TABLE_01"],
      views: ["APP.VIEW_02"],
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

type MockDbObjectSummary = {
  name: string;
  owner: string;
  object_type: string;
  row_count: number | null;
  comment: string;
};

// backend GET /nl2sql/profiles/{id}/ontology-view の profile スコープ済み応答(縮約)。
// 列・schema ノードは画面側で省かれるため、表示は表 2 + ビュー 1 = 3 ノード、FK 1 関係になる。
const profileOntologyView = {
  profile_ontology_view: {
    id: "profile-view:default",
    profile_id: "default",
    ontology_revision_id: "ontology_revision:fp:3",
    etag: "view-etag-1",
    node_ids: [
      "table:APP:TABLE_01",
      "table:APP:TABLE_03",
      "view:APP:VIEW_02",
      "column:APP:TABLE_01:ID",
    ],
    edge_ids: ["fk:APP:TABLE_03:FK_T3_T1", "contains:APP:TABLE_01:ID"],
    allowed_path_ids: [],
    table_usages_ja: {},
    draft_node_overrides: [],
    draft_edge_overrides: [],
  },
  ontology_graph: {
    revision: {
      id: "ontology_revision:fp:3",
      version: 3,
      status: "published",
      schema_fingerprint: "fp",
      etag: "rev-etag-3",
    },
    nodes: [
      {
        id: "table:APP:TABLE_01",
        kind: "table",
        business_name_ja: "表論理名_01",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "TABLE_01", object_type: "table" } },
        ],
      },
      {
        id: "table:APP:TABLE_03",
        kind: "table",
        business_name_ja: "表論理名_03",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "TABLE_03", object_type: "table" } },
        ],
      },
      {
        id: "view:APP:VIEW_02",
        kind: "view",
        business_name_ja: "ビュー論理名_02",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "VIEW_02", object_type: "view" } },
        ],
      },
      {
        id: "column:APP:TABLE_01:ID",
        kind: "column",
        business_name_ja: "ID",
        review_status: "approved",
      },
    ],
    edges: [
      {
        id: "fk:APP:TABLE_03:FK_T3_T1",
        kind: "foreign_key",
        source_node_id: "table:APP:TABLE_03",
        target_node_id: "table:APP:TABLE_01",
        relationship_name_ja: "表論理名_01 を参照",
        cardinality: "many_to_one",
        review_status: "approved",
      },
      {
        id: "contains:APP:TABLE_01:ID",
        kind: "contains",
        source_node_id: "table:APP:TABLE_01",
        target_node_id: "column:APP:TABLE_01:ID",
        relationship_name_ja: "含む",
        review_status: "approved",
      },
    ],
  },
};

async function mockProfileApi(
  page: Page,
  options: {
    catalog?: typeof schemaCatalog;
    tableItems?: MockDbObjectSummary[];
    viewItems?: MockDbObjectSummary[];
    profileItems?: typeof profiles;
    dbProfileData?: typeof dbProfiles;
  } = {}
) {
  const tableItems = options.tableItems ?? [
    { name: "TABLE_01", owner: "APP", object_type: "TABLE", row_count: null, comment: "table" },
    { name: "SYS$AUDIT", owner: "SYS", object_type: "TABLE", row_count: null, comment: "system" },
  ];
  const viewItems = options.viewItems ?? [
    { name: "VIEW_02", owner: "APP", object_type: "VIEW", row_count: null, comment: "view" },
    { name: "V_$SESSION", owner: "SYS", object_type: "VIEW", row_count: null, comment: "system" },
  ];
  const catalog = options.catalog ?? schemaCatalog;
  const profileItems = options.profileItems ?? profiles;
  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, options.catalog ?? schemaCatalog));
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "cross-schema-test",
      refreshed_at: catalog.refreshed_at,
      object_count: catalog.tables.length,
      column_count: 0,
      change_token: 1,
      etag: "cross-schema-test",
    })
  );
  await page.route("**/api/schema/owners", (route) => {
    const counts = new Map<string, { table_count: number; view_count: number }>();
    for (const object of catalog.tables.filter((item) => !item.table_name.includes("$"))) {
      const current = counts.get(object.owner) ?? { table_count: 0, view_count: 0 };
      if (["VIEW", "MATERIALIZED VIEW"].includes(object.table_type.toUpperCase())) {
        current.view_count += 1;
      } else {
        current.table_count += 1;
      }
      counts.set(object.owner, current);
    }
    return fulfillJson(route, {
      current_owner: "APP",
      owners: [...counts.entries()].map(([owner, value]) => ({
        owner,
        is_current: owner === "APP",
        ...value,
      })),
      excluded_oracle_maintained_count: 2,
    });
  });
  await page.route("**/api/schema/objects?*", (route) => {
    const url = new URL(route.request().url());
    const type = url.searchParams.get("type") ?? "";
    const owner = (url.searchParams.get("owner") ?? "").toUpperCase();
    const query = (url.searchParams.get("q") ?? "").toLowerCase();
    const items = catalog.tables
      .filter((object) => !owner || object.owner.toUpperCase() === owner)
      .filter((object) => !type || object.table_type.toUpperCase() === type.toUpperCase())
      .filter((object) =>
        [
          object.owner,
          `${object.owner}.${object.table_name}`,
          object.table_name,
          object.logical_name,
          object.comment,
        ]
          .join(" ")
          .toLowerCase()
          .includes(query)
      )
      .map((object) => ({
        owner: object.owner,
        object_name: object.table_name,
        object_type: object.table_type,
        logical_name: object.logical_name,
        comment: object.comment,
        row_count: object.row_count,
        column_count: object.columns.length,
        last_ddl_at: "",
      }));
    return fulfillJson(route, {
      items,
      next_cursor: null,
      total: items.length,
      catalog_version: 1,
    });
  });
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
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?business_profiles_only=true&include_archived_business_profiles=true",
    (route) => fulfillJson(route, options.dbProfileData ?? dbProfiles)
  );
  await page.route("**/api/nl2sql/profiles", async (route) => {
    if (route.request().method() === "GET") {
      await fulfillJson(route, profileItems);
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: profileItems.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: Object.keys(profile.glossary).length,
        few_shot_count: profile.few_shot_examples.length,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-07-19T00:00:00Z",
      })),
      next_cursor: null,
      total: profileItems.length,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/*", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/search")) return route.fallback();
    const profileId = pathname.split("/").at(-1);
    const profile = profileItems.find((item) => item.id === profileId) ?? profileItems[0];
    return fulfillJson(route, { ...profile, etag: `etag-${profile.id}` });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) =>
    fulfillJson(route, profileOntologyView)
  );
  await page.route("**/api/nl2sql/profiles/*/oracle-sync-jobs", (route) =>
    fulfillJson(route, {
      job_id: "profile-sync-default",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "queued",
      phase: "queued",
      rebuild_agent_assets: false,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-22T00:00:00Z",
    })
  );
  await page.route("**/api/nl2sql/oracle-sync-jobs/*", (route) =>
    fulfillJson(route, {
      job_id: "profile-sync-default",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "succeeded",
      phase: "succeeded",
      rebuild_agent_assets: false,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-22T00:00:00Z",
      finished_at: "2026-07-22T00:00:01Z",
      oracle_result: {
        runtime: "oracle",
        executed: true,
        status: "saved",
        profile_name: "NL2SQL_DEFAULT_PROFILE",
        original_name: "",
        ddl: [],
        profile: dbProfiles.profiles[0],
        warnings: [],
        engine_meta: {},
      },
    })
  );
}

test("業務プロファイルの更新操作はテーブル管理と同じ文言・順序・階層で動作する", async ({ page }) => {
  let profileSearchRequests = 0;
  let schemaRefreshSubmissions = 0;
  let schemaRefreshPolls = 0;

  page.on("request", (request) => {
    if (
      request.method() === "GET" &&
      new URL(request.url()).pathname === "/api/nl2sql/profiles/search"
    ) {
      profileSearchRequests += 1;
    }
  });
  await mockProfileApi(page);
  await page.route("**/api/schema/refresh-jobs**", async (route) => {
    if (route.request().method() === "POST") {
      schemaRefreshSubmissions += 1;
      await fulfillJson(route, {
        job_id: "profile-schema-refresh",
        status: "pending",
        created_at: "2026-07-23T00:00:00Z",
        scanned_objects: 0,
        changed_objects: 0,
        deleted_objects: 0,
        catalog_version: 1,
        error_code: "",
      });
      return;
    }

    schemaRefreshPolls += 1;
    const done = schemaRefreshPolls >= 2;
    await fulfillJson(route, {
      job_id: "profile-schema-refresh",
      status: done ? "done" : "running",
      created_at: "2026-07-23T00:00:00Z",
      scanned_objects: done ? schemaCatalog.tables.length : 10,
      changed_objects: done ? 2 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : 1,
      error_code: "",
    });
  });

  await page.goto("/profiles");

  await expect(page.getByRole("region", { name: "業務プロファイル状態" })).toHaveCount(0);
  await expect(page.getByText("許可オブジェクト", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Oracle Profile", { exact: true })).toHaveCount(0);

  const actions = page.getByTestId("profile-management-actions");
  const isMobile = (page.viewportSize()?.width ?? 0) < 640;
  const createButton = actions.getByRole("button", { name: "新規作成", exact: true });
  await expect(createButton).toHaveAttribute("data-page-action-kind", "primary");

  if (isMobile) {
    const moreButton = actions.getByRole("button", { name: "その他の操作", exact: true });
    await expect(actions.getByRole("button")).toHaveText(["新規作成", "その他の操作"]);
    await createButton.focus();
    await page.keyboard.press("Tab");
    await expect(moreButton).toBeFocused();
    await moreButton.press("Enter");

    const menu = page.getByRole("menu");
    await expect(moreButton).toHaveAttribute("aria-expanded", "true");
    await expect(menu.getByRole("menuitem")).toHaveText(["表示を更新", "DB 構造を再取得"]);
    await expect(menu.getByRole("menuitem", { name: "表示を更新" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(moreButton).toBeFocused();
    await expect(moreButton).toHaveAttribute("aria-expanded", "false");

    const requestsBeforeRefresh = profileSearchRequests;
    await moreButton.click();
    await menu.getByRole("menuitem", { name: "表示を更新" }).click();
    await expect.poll(() => profileSearchRequests).toBeGreaterThan(requestsBeforeRefresh);
    expect(schemaRefreshSubmissions).toBe(0);

    await moreButton.click();
    await menu.getByRole("menuitem", { name: "DB 構造を再取得" }).click();
    await expect.poll(() => schemaRefreshSubmissions).toBe(1);
    await expect(page.getByText("スキーマ更新: 実行中", { exact: true })).toBeVisible();
    await moreButton.click();
    await expect(menu.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeDisabled();
    await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeEnabled();
  } else {
    const actionButtons = actions.getByRole("button");
    const refreshButton = actions.getByRole("button", { name: "表示を更新", exact: true });
    const schemaRefreshButton = actions.getByRole("button", {
      name: "DB 構造を再取得",
      exact: true,
    });

    await expect(actionButtons).toHaveText(["新規作成", "表示を更新", "DB 構造を再取得"]);
    await expect(createButton).toHaveClass(/\bbg-primary\b/);
    await expect(refreshButton).toHaveClass(/\bbg-card\b/);
    await expect(schemaRefreshButton).toHaveClass(/\bbg-card\b/);

    const [createBox, refreshBox, schemaRefreshBox] = await Promise.all([
      createButton.boundingBox(),
      refreshButton.boundingBox(),
      schemaRefreshButton.boundingBox(),
    ]);
    expect(createBox).not.toBeNull();
    expect(refreshBox).not.toBeNull();
    expect(schemaRefreshBox).not.toBeNull();
    expect(refreshBox!.x).toBeGreaterThan(createBox!.x);
    expect(schemaRefreshBox!.x).toBeGreaterThan(refreshBox!.x);

    await createButton.focus();
    await page.keyboard.press("Tab");
    await expect(refreshButton).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(schemaRefreshButton).toBeFocused();

    const requestsBeforeRefresh = profileSearchRequests;
    await refreshButton.click();
    await expect.poll(() => profileSearchRequests).toBeGreaterThan(requestsBeforeRefresh);
    expect(schemaRefreshSubmissions).toBe(0);

    await schemaRefreshButton.click();
    await expect.poll(() => schemaRefreshSubmissions).toBe(1);
    await expect(page.getByText("スキーマ更新: 実行中", { exact: true })).toBeVisible();
    await expect(schemaRefreshButton).toBeDisabled();
    await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toBeVisible();
    await expect(schemaRefreshButton).toBeEnabled();
  }

  const viewportWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(viewportWidth.scrollWidth).toBeLessThanOrEqual(viewportWidth.clientWidth + 1);
});

test("業務プロファイルは表とビューを固定高リストで管理できる", async ({ page }) => {
  let savedPayload: Record<string, unknown> | null = null;
  let oracleSyncPayload: Record<string, unknown> | null = null;
  await mockProfileApi(page);
  await page.route("**/api/nl2sql/profiles/default**", async (route) => {
    if (route.request().url().includes("/ontology-view")) {
      await route.fallback();
      return;
    }
    savedPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, { ...profiles[0], ...savedPayload, id: "default" });
  });
  await page.route("**/api/nl2sql/profiles/default/oracle-sync-jobs", async (route) => {
    oracleSyncPayload = route.request().postDataJSON() as Record<string, unknown>;
    await fulfillJson(route, {
      job_id: "profile-sync-default",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "queued",
      phase: "queued",
      rebuild_agent_assets: false,
      error_code: "",
      error_message_ja: "",
      created_at: "2026-07-22T00:00:00Z",
    });
  });

  await page.goto("/profiles");

  const actions = page.getByTestId("profile-management-actions");
  await expect(actions).toBeVisible();
  const refreshButton = actions.getByRole("button", { name: "表示を更新", exact: true });
  if (await refreshButton.isVisible()) {
    await expect(refreshButton).toBeVisible();
  } else {
    const moreButton = actions.getByRole("button", { name: "その他の操作", exact: true });
    await moreButton.click();
    await expect(page.getByRole("menuitem", { name: "表示を更新", exact: true })).toBeVisible();
    await page.keyboard.press("Escape");
  }

  await expect(page.getByRole("tab", { name: "一覧", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "一覧と詳細" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Oracle Profile", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "新規作成", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新規プロファイル" })).toBeVisible();
  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  const profileRow = page.getByRole("row").filter({ hasText: "既定プロファイル" });
  await profileRow.getByRole("button", { name: "編集", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 既定プロファイル" })
  ).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("既定プロファイル");
  await expect(page.getByRole("button", { name: "Drop 実行" })).toHaveCount(0);

  const glossaryField = page.getByRole("textbox", { name: "語彙・同義語" });
  const fewShotField = page.getByRole("textbox", { name: "few-shot 例" });
  await expect(glossaryField).toBeVisible();
  await expect(fewShotField).toBeVisible();
  await expect(page.getByRole("textbox", { name: "SQL ルール" })).toHaveCount(0);
  const [glossaryBox, fewShotBox] = await Promise.all([
    glossaryField.boundingBox(),
    fewShotField.boundingBox(),
  ]);
  expect(glossaryBox).not.toBeNull();
  expect(fewShotBox).not.toBeNull();
  if ((page.viewportSize()?.width ?? 0) >= 1024) {
    expect(fewShotBox!.x).toBeGreaterThan(glossaryBox!.x);
    expect(Math.abs(fewShotBox!.y - glossaryBox!.y)).toBeLessThanOrEqual(1);
  } else {
    expect(fewShotBox!.y).toBeGreaterThan(glossaryBox!.y);
    expect(fewShotBox!.width).toBeGreaterThanOrEqual(glossaryBox!.width - 1);
  }

  // 対象オブジェクト選択はタブなしで常時表示される
  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");
  await expect(tableList.getByText("APP.TABLE_01", { exact: true })).toBeVisible();
  await expect(viewList.getByText("APP.VIEW_02", { exact: true })).toBeVisible();
  await expect(tableList.getByText("APP.VIEW_02", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("APP.TABLE_01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("SYS$AUDIT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("V_$SESSION", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("表論理名_01", { exact: true })).toBeVisible();
  await expect(viewList.getByText("ビュー論理名_02", { exact: true })).toBeVisible();

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

  await expect(tableList.getByLabel("APP.TABLE_01")).toBeChecked();
  await expect(viewList.getByLabel("APP.VIEW_02")).toBeChecked();
  await objectSearch.fill("03");
  await expect(tableList.getByText("APP.TABLE_03", { exact: true })).toBeVisible();
  await expect(viewList.getByText("APP.VIEW_03", { exact: true })).toBeVisible();
  await expect(tableList.getByText("APP.TABLE_01", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("APP.VIEW_02", { exact: true })).toHaveCount(0);
  await objectSearch.clear();
  await expect(tableList.getByLabel("APP.TABLE_01")).toBeChecked();
  await expect(viewList.getByLabel("APP.VIEW_02")).toBeChecked();

  const tableScrollRegion = page.getByTestId("profile-allowed-table-list-scroll-region");
  const fit = await tableList.evaluate((node) => {
    const listBox = node.getBoundingClientRect();
    const rows = Array.from(node.querySelectorAll("label")).map((row) => row.getBoundingClientRect());
    const visibleRows = rows.filter((row) => row.bottom <= listBox.bottom + 1 && row.top >= listBox.top - 1).length;
    return {
      listHeight: listBox.height,
      visibleRows,
      totalRows: rows.length,
      noHorizontalOverflow: node.scrollWidth <= node.clientWidth + 1,
    };
  });
  const scrollMetrics = await tableScrollRegion.evaluate((node) => ({
    clientHeight: node.clientHeight,
    scrollHeight: node.scrollHeight,
    overflowY: window.getComputedStyle(node).overflowY,
  }));
  expect(fit.listHeight).toBeGreaterThanOrEqual(388);
  expect(fit.listHeight).toBeLessThanOrEqual(396);
  expect(fit.visibleRows).toBeGreaterThan(0);
  expect(fit.visibleRows).toBeLessThan(fit.totalRows);
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);
  expect(scrollMetrics.overflowY).toBe("auto");
  expect(fit.noHorizontalOverflow).toBe(true);

  await tableList.getByLabel("APP.TABLE_03").check();
  await viewList.getByLabel("APP.VIEW_04").check();
  const roleField = page.getByLabel("アシスタントロール");
  const instructionsField = page.getByLabel("追加指示", { exact: true });
  const roleBox = await roleField.boundingBox();
  const instructionsBox = await instructionsField.boundingBox();
  expect(roleBox?.height).toBe(instructionsBox?.height);
  await roleField.fill("財務分析向け Oracle SQL アシスタント");
  await instructionsField.fill("日付は DATE 型で返す。");
  // ADMIN_EXECUTE ゲートを満たすと保存ボタンが有効になり、Oracle 反映 job を投入する。
  const saveButton = page.getByRole("button", { name: "保存", exact: true });
  await expect(saveButton).toBeDisabled();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  // 業務 Profile 保存後はすぐに解除され、Oracle 反映は polling で完了を受け取る。
  await expect(page.getByTestId("profile-oracle-result").getByText("saved")).toBeVisible();
  const payload = savedPayload as {
    allowed_tables: string[];
    allowed_views: string[];
    select_ai_config: Record<string, unknown>;
  } | null;
  expect(payload?.allowed_tables).toEqual(["APP.TABLE_01", "APP.TABLE_03"]);
  expect(payload?.allowed_views).toEqual(["APP.VIEW_02", "APP.VIEW_04"]);
  expect(payload).toHaveProperty("sql_rules", []);
  expect(payload?.select_ai_config).toMatchObject({
    embedding_model: "cohere.embed-v4.0",
    enforce_object_list: true,
    role: "財務分析向け Oracle SQL アシスタント",
    additional_instructions: "日付は DATE 型で返す。",
  });

  expect(oracleSyncPayload).toMatchObject({
    confirmation: "ADMIN_EXECUTE",
    reason: "ui-profile-management-save",
    rebuild_agent_assets: false,
  });
  expect(oracleSyncPayload).not.toHaveProperty("execute");

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
  const [mobileGlossaryBox, mobileFewShotBox] = await Promise.all([
    glossaryField.boundingBox(),
    fewShotField.boundingBox(),
  ]);
  expect(mobileGlossaryBox).not.toBeNull();
  expect(mobileFewShotBox).not.toBeNull();
  expect(mobileFewShotBox!.y).toBeGreaterThan(mobileGlossaryBox!.y);
  expect(mobileFewShotBox!.width).toBeGreaterThanOrEqual(mobileGlossaryBox!.width - 1);

  const bodyWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
});

test("Oracle 反映失敗を明示し Ontology に触れず再試行できる", async ({ page }) => {
  await mockProfileApi(page);
  let ontologyRequests = 0;
  let retryCalls = 0;
  let retried = false;
  page.on("request", (request) => {
    if (request.url().includes("/ontology-view")) ontologyRequests += 1;
  });
  await page.route("**/api/nl2sql/oracle-sync-jobs/*/retry", async (route) => {
    retryCalls += 1;
    retried = true;
    await fulfillJson(route, {
      job_id: "profile-sync-retry",
      profile_id: "default",
      profile_etag: "etag-default",
      status: "queued",
      phase: "queued",
      rebuild_agent_assets: false,
      error_code: "",
      error_message_ja: "",
      retry_of_job_id: "profile-sync-default",
      created_at: "2026-07-22T00:00:02Z",
    });
  });
  await page.route("**/api/nl2sql/oracle-sync-jobs/*", async (route) => {
    await fulfillJson(route, {
      job_id: retried ? "profile-sync-retry" : "profile-sync-default",
      profile_id: "default",
      profile_etag: "etag-default",
      status: retried ? "succeeded" : "failed",
      phase: retried ? "succeeded" : "failed",
      rebuild_agent_assets: false,
      error_code: retried ? "" : "ORACLE_TIMEOUT",
      error_message_ja: retried ? "" : "Oracle 呼出しが 120 秒でタイムアウトしました。",
      created_at: "2026-07-22T00:00:00Z",
      finished_at: "2026-07-22T00:00:01Z",
      oracle_result: retried
        ? {
            runtime: "oracle",
            executed: true,
            status: "saved",
            profile_name: "NL2SQL_DEFAULT_PROFILE",
            original_name: "",
            ddl: [],
            profile: dbProfiles.profiles[0],
            warnings: [],
            engine_meta: {},
          }
        : null,
    });
  });

  await page.goto("/profiles");
  const profileRow = page.getByRole("row").filter({ hasText: "既定プロファイル" });
  await profileRow.getByRole("button", { name: "編集", exact: true }).click();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  const save = page.getByRole("button", { name: "保存", exact: true });
  await save.click();
  await expect(save).toBeEnabled();

  const status = page.getByTestId("profile-oracle-sync-status");
  await expect(status).toContainText(
    "業務 Profile は保存されましたが、Oracle 反映に失敗しました。"
  );
  await expect(status).toContainText("Oracle 呼出しが 120 秒でタイムアウトしました。");
  expect(ontologyRequests).toBe(0);

  const retry = status.getByRole("button", { name: "Oracle 反映を再試行" });
  await retry.focus();
  await expect(retry).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(status).toContainText("Oracle 反映: 完了");
  expect(retryCalls).toBe(1);
  expect(ontologyRequests).toBe(0);
});

test("異なる schema の同名表を別々に選択できる", async ({ page }) => {
  const duplicateCatalog = {
    ...schemaCatalog,
    tables: [
      ...schemaCatalog.tables,
      {
        table_name: "ORDERS",
        qualified_name: "APP.ORDERS",
        logical_name: "アプリ受注",
        owner: "APP",
        table_type: "TABLE",
        comment: "APP の受注",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "ORDERS",
        qualified_name: "SH.ORDERS",
        logical_name: "販売受注",
        owner: "SH",
        table_type: "TABLE",
        comment: "SH の受注",
        row_count: null,
        columns: [],
        constraints: [],
      },
    ],
  };
  const duplicateProfiles = [
    {
      ...profiles[0],
      allowed_tables: ["APP.ORDERS", "SH.ORDERS"],
      allowed_views: [],
    },
  ];
  await mockProfileApi(page, {
    catalog: duplicateCatalog,
    profileItems: duplicateProfiles,
  });

  await page.goto("/profiles?profile=default");

  const tableList = page.getByTestId("profile-allowed-table-list");
  await expect(tableList.getByLabel("APP.ORDERS")).toBeChecked();
  await expect(tableList.getByLabel("SH.ORDERS")).toBeChecked();
  await expect(tableList.getByText("APP", { exact: true })).toBeVisible();
  await expect(tableList.getByText("SH", { exact: true })).toBeVisible();

  await tableList.getByLabel("SH.ORDERS").uncheck();
  await expect(tableList.getByLabel("APP.ORDERS")).toBeChecked();
  await expect(tableList.getByLabel("SH.ORDERS")).not.toBeChecked();

  await tableList.getByRole("checkbox", { name: "SH schema を一括選択" }).click();
  await expect(tableList.getByLabel("SH.ORDERS")).toBeChecked();

  await page.getByRole("searchbox", { name: "オブジェクト検索" }).fill("SH.ORDERS");
  await expect(tableList.getByLabel("SH.ORDERS")).toBeVisible();
  await expect(tableList.getByLabel("APP.ORDERS")).toHaveCount(0);
});

test("Oracle Profile の Region と Max Tokens は狭い編集ペインでも重ならない", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles");

  await page.getByRole("button", { name: "新規作成", exact: true }).click();

  const region = page.getByLabel("Region");
  const maxTokens = page.getByLabel("Max Tokens");
  await expect(region).toBeVisible();
  await expect(maxTokens).toBeVisible();

  const [regionBox, maxTokensBox] = await Promise.all([region.boundingBox(), maxTokens.boundingBox()]);
  expect(regionBox).not.toBeNull();
  expect(maxTokensBox).not.toBeNull();
  const regionRight = (regionBox?.x ?? 0) + (regionBox?.width ?? 0);
  const maxTokensRight = (maxTokensBox?.x ?? 0) + (maxTokensBox?.width ?? 0);
  const regionBottom = (regionBox?.y ?? 0) + (regionBox?.height ?? 0);
  const maxTokensBottom = (maxTokensBox?.y ?? 0) + (maxTokensBox?.height ?? 0);
  expect(
    regionRight <= (maxTokensBox?.x ?? 0) ||
      maxTokensRight <= (regionBox?.x ?? 0) ||
      regionBottom <= (maxTokensBox?.y ?? 0) ||
      maxTokensBottom <= (regionBox?.y ?? 0)
  ).toBe(true);
});

test("名称未入力で保存すると名称欄直下に FieldError が出る", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles");

  await page.getByRole("button", { name: "新規作成", exact: true }).click();

  // ADMIN_EXECUTE ゲートを満たして保存ボタンを有効化するが、名称は空のまま保存する。
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  // spec §2 error-placement: 該当欄の直下に role=alert で表示される。
  const fieldError = page.getByRole("alert").filter({ hasText: "名称を入力してください。" });
  await expect(fieldError).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveAttribute("aria-invalid", "true");

  // 入力し直すとエラーは消える。
  await page.getByLabel("名称").fill("新プロファイル");
  await expect(fieldError).toHaveCount(0);
});

test("業務プロファイルはcatalogが空のときDB管理用の現在schema一覧を混在させない", async ({ page }) => {
  await mockProfileApi(page, {
    catalog: { ...schemaCatalog, tables: [] },
    tableItems: [
      { name: "DEPARTMENT", owner: "APP", object_type: "TABLE", row_count: 10, comment: "部署" },
      { name: "EMPLOYEE", owner: "APP", object_type: "TABLE", row_count: 12, comment: "社員" },
      { name: "PROJECT", owner: "APP", object_type: "TABLE", row_count: 8, comment: "案件" },
      { name: "SYS$AUDIT", owner: "SYS", object_type: "TABLE", row_count: null, comment: "system" },
    ],
    viewItems: [
      { name: "V_EMP_DEPT", owner: "APP", object_type: "VIEW", row_count: null, comment: "社員と部署" },
    ],
    profileItems: [{ ...profiles[0], allowed_tables: [], allowed_views: [] }],
  });
  await page.goto("/profiles");
  await page.getByRole("button", { name: /^既定プロファイル/ }).click();

  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");

  await expect(tableList.getByText("選択できるテーブルがありません。")).toBeVisible();
  await expect(tableList.getByText("DEPARTMENT", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("EMPLOYEE", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("PROJECT", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("SYS$AUDIT", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("V_EMP_DEPT", { exact: true })).toHaveCount(0);
});

test("業務プロファイルの対象オブジェクト空状態はExcelプレビュー風の広い面で表示する", async ({ page }) => {
  await mockProfileApi(page, {
    catalog: { ...schemaCatalog, tables: [] },
    tableItems: [],
    viewItems: [],
  });

  await page.goto("/profiles");
  await page.getByRole("button", { name: /^既定プロファイル/ }).click();

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

test("未解決オブジェクトの警告からスキーマ情報を更新して復旧できる", async ({ page }) => {
  await mockProfileApi(page);
  let schemaRefreshed = false;
  let refreshJobPolls = 0;
  let ontologyViewCalls = 0;
  await page.route("**/api/schema/refresh-jobs**", async (route) => {
    if (route.request().method() === "POST") {
      await fulfillJson(route, {
        job_id: "schema-refresh-1",
        status: "pending",
        created_at: "2026-07-12T00:00:00Z",
        scanned_objects: 0,
        changed_objects: 0,
        deleted_objects: 0,
        catalog_version: 1,
        error_code: "",
      });
      return;
    }
    refreshJobPolls += 1;
    const done = refreshJobPolls >= 2;
    schemaRefreshed = done;
    await fulfillJson(route, {
      job_id: "schema-refresh-1",
      status: done ? "done" : "running",
      created_at: "2026-07-12T00:00:00Z",
      scanned_objects: done ? 1 : 0,
      changed_objects: done ? 1 : 0,
      deleted_objects: 0,
      catalog_version: done ? 2 : 1,
      error_code: "",
    });
  });
  // 初回は未解決警告つき空グラフ、スキーマ更新後は解決済みグラフを返す
  await page.route("**/api/nl2sql/profiles/*/ontology-view", async (route) => {
    ontologyViewCalls += 1;
    if (!schemaRefreshed) {
      await fulfillJson(route, {
        profile_ontology_view: {
          ...profileOntologyView.profile_ontology_view,
          node_ids: [],
          edge_ids: [],
        },
        ontology_graph: { ...profileOntologyView.ontology_graph, nodes: [], edges: [] },
        warnings_ja: [
          "「TABLE_01」を公開 Ontology(スキーマ情報)に解決できません。スキーマ情報を更新するか、オブジェクト名(owner 付き)を確認してください。",
        ],
      });
      return;
    }
    await fulfillJson(route, { ...profileOntologyView, warnings_ja: [] });
  });

  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);

  const unresolved = page.getByTestId("profile-ontology-unresolved");
  await expect(unresolved).toBeVisible();
  await expect(unresolved.getByText("TABLE_01", { exact: false })).toBeVisible();

  await unresolved.getByRole("button", { name: "スキーマ情報を更新" }).click();

  await expect(page.getByText("スキーマ更新: 実行中", { exact: true })).toBeVisible();
  await expect(unresolved).toBeVisible();
  await expect(page.getByTestId("profile-ontology-unresolved")).toHaveCount(0);
  await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toBeVisible();
  await expect(page.getByText("3 ノード", { exact: true })).toBeVisible();
  expect(schemaRefreshed).toBe(true);
  expect(ontologyViewCalls).toBeGreaterThanOrEqual(2);
});

test("Ontology 未公開のとき物理・業務モデルは整った空状態カードで導線を示す", async ({ page }) => {
  await mockProfileApi(page);
  // 公開済み Ontology が無い(graph=null)状態を返す。
  await page.route("**/api/nl2sql/profiles/*/ontology-view", async (route) => {
    await fulfillJson(route, { ontology_graph: null, warnings_ja: [] });
  });

  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);

  const empty = page.getByTestId("profile-ontology-empty");
  await expect(empty).toBeVisible();
  // セクション見出しとして成立し、正確な誘導文(公開が条件)が出ている。
  await expect(empty.getByRole("heading", { name: "物理・業務モデル編集" })).toBeVisible();
  await expect(empty.getByText("物理・業務モデルはまだ表示できません")).toBeVisible();
  await expect(empty.getByText("Ontology を公開", { exact: false })).toBeVisible();
});
