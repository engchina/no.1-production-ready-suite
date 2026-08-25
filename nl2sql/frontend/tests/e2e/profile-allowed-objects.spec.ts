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
    schemaObjectTotals?: Partial<Record<"TABLE" | "VIEW", number>>;
    profileSearchTotal?: number;
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
    const total = options.schemaObjectTotals?.[type.toUpperCase() as "TABLE" | "VIEW"] ?? items.length;
    return fulfillJson(route, {
      items,
      next_cursor: null,
      total,
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
  await page.route("**/api/nl2sql/profiles/search?*", (route) => {
    const query = (new URL(route.request().url()).searchParams.get("q") ?? "").trim().toLowerCase();
    const filteredProfiles = profileItems.filter(
      (profile) =>
        !profile.archived &&
        (!query ||
          profile.name.toLowerCase().includes(query) ||
          (profile.category ?? "").toLowerCase().includes(query) ||
          (profile.description ?? "").toLowerCase().includes(query))
    );
    const total = query ? filteredProfiles.length : (options.profileSearchTotal ?? filteredProfiles.length);
    return fulfillJson(route, {
      items: filteredProfiles.map((profile) => ({
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
      next_cursor: total > filteredProfiles.length ? "profile-page-2" : null,
      total,
      change_token: 1,
    });
  });
  await page.route("**/api/nl2sql/profiles/*", (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/search")) return route.fallback();
    const detailMatch = pathname.match(/^\/api\/nl2sql\/profiles\/([^/]+)$/);
    if (!detailMatch) return route.fallback();
    const profileId = detailMatch[1];
    const profile = profileItems.find((item) => item.id === profileId) ?? profileItems[0];
    return fulfillJson(route, { ...profile, etag: `etag-${profile.id}` });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) =>
    fulfillJson(route, profileOntologyView)
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: null,
      draft_etag: "",
      published_at: null,
    })
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

async function expectNoDocumentHorizontalOverflow(page: Page) {
  const width = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    bodyScrollWidth: document.body.scrollWidth,
    bodyClientWidth: document.body.clientWidth,
  }));
  expect(width.scrollWidth).toBeLessThanOrEqual(width.clientWidth + 1);
  expect(width.bodyScrollWidth).toBeLessThanOrEqual(width.bodyClientWidth + 1);
}

test("業務プロファイル一覧検索はAPI totalを件数表示に使い検索結果だけを表示する", async ({ page }) => {
  const searchableProfiles = [
    {
      ...profiles[0],
      id: "profile-dept",
      name: "PROFILE_DEPT",
      category: "DEPT",
      description: "部署 profile",
      allowed_tables: ["APP.TABLE_01"],
      allowed_views: [],
    },
    {
      ...profiles[0],
      id: "profile-emp",
      name: "PROFILE_EMP",
      category: "EMPLOYEE",
      description: "社員 profile",
      allowed_tables: ["APP.TABLE_02"],
      allowed_views: [],
    },
  ];
  await mockProfileApi(page, {
    profileItems: searchableProfiles,
    profileSearchTotal: 128,
  });

  await page.goto("/profiles");

  const listPanel = page.locator("#profile-management-panel-list");
  await expect(listPanel.getByText("128 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("profile-management-footer")).toContainText("2 / 128 件を表示");
  await expect(listPanel.getByText("PROFILE_DEPT", { exact: true })).toBeVisible();
  await expect(listPanel.getByText("PROFILE_EMP", { exact: true })).toBeVisible();
  await expectNoDocumentHorizontalOverflow(page);

  const search = page.getByRole("searchbox", { name: "プロファイル検索" });
  await search.fill("PROFILE_DEPT");

  await expect(listPanel.getByText("PROFILE_DEPT", { exact: true })).toBeVisible();
  await expect(listPanel.getByText("PROFILE_EMP", { exact: true })).toHaveCount(0);
  await expect(listPanel.getByText("1 件", { exact: true })).toBeVisible();
  await expect(page.getByTestId("profile-management-footer")).toContainText("1 / 1 件を表示");

  await search.fill("NO_MATCH_PROFILE");
  await expect(listPanel.getByText("一致するプロファイルがありません")).toBeVisible();
  await expect(page.getByTestId("profile-management-footer")).toHaveCount(0);

  await page.setViewportSize({ width: 375, height: 900 });
  await search.fill("PROFILE_EMP");
  await expect(listPanel.getByText("PROFILE_EMP", { exact: true })).toBeVisible();
  await expect(listPanel.getByText("PROFILE_DEPT", { exact: true })).toHaveCount(0);
  await expectNoDocumentHorizontalOverflow(page);
});

test("業務プロファイルの対象オブジェクト件数は取得済み件数と API total を分けて表示する", async ({ page }) => {
  const largeCatalog = {
    ...schemaCatalog,
    tables: [
      ...Array.from({ length: 102 }, (_, index) => {
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
      ...Array.from({ length: 2 }, (_, index) => {
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
    ],
  };
  const countProfiles = [
    {
      ...profiles[0],
      id: "count-profile",
      name: "件数確認プロファイル",
      allowed_tables: ["APP.TABLE_01"],
      allowed_views: ["APP.VIEW_01", "APP.VIEW_02"],
    },
  ];

  await mockProfileApi(page, {
    catalog: largeCatalog,
    profileItems: countProfiles,
    schemaObjectTotals: { TABLE: 310, VIEW: 125 },
  });

  await page.goto("/profiles?profile=count-profile");

  await expect(page.getByTestId("profile-object-search-toolbar")).toContainText(
    "104 / 435 件を表示、選択 3 件"
  );
  await expect(page.getByTestId("profile-allowed-table-list-footer")).toContainText(
    "102 / 310 件を表示、選択 1 件"
  );
  await expect(page.getByTestId("profile-allowed-view-list-footer")).toContainText(
    "2 / 125 件を表示、選択 2 件"
  );
});

test("業務プロファイル基本情報は名称とカテゴリの行を揃えて表示する", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles?profile=new");

  await expect(page.getByRole("heading", { name: "新規プロファイル" })).toBeVisible();

  const nameInput = page.locator("#profile-name");
  const categoryInput = page.locator("#profile-category");
  await expect(nameInput).toBeVisible();
  await expect(categoryInput).toBeVisible();
  await expect(nameInput).toHaveAccessibleName("名称 必須");
  await expect(categoryInput).toHaveAccessibleName("カテゴリ 必須");

  const layout = await page.evaluate(() => {
    const box = (selector: string) => {
      const rect = document.querySelector(selector)?.getBoundingClientRect();
      if (!rect) return null;
      return {
        x: rect.x,
        y: rect.y,
        width: rect.width,
        height: rect.height,
        right: rect.right,
        bottom: rect.bottom,
      };
    };
    return {
      viewportWidth: window.innerWidth,
      nameLabel: box('label[for="profile-name"]'),
      categoryLabel: box('label[for="profile-category"]'),
      nameInput: box("#profile-name"),
      categoryInput: box("#profile-category"),
      helper: box("#profile-name-helper"),
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    };
  });

  expect(layout.nameLabel).not.toBeNull();
  expect(layout.categoryLabel).not.toBeNull();
  expect(layout.nameInput).not.toBeNull();
  expect(layout.categoryInput).not.toBeNull();
  expect(layout.helper).not.toBeNull();
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);

  if (layout.viewportWidth >= 768) {
    expect(Math.abs(layout.nameLabel!.y - layout.categoryLabel!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.nameInput!.y - layout.categoryInput!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.nameInput!.height - layout.categoryInput!.height)).toBeLessThanOrEqual(1);
    expect(Math.abs(layout.nameInput!.width - layout.categoryInput!.width)).toBeLessThanOrEqual(1);
    expect(layout.helper!.y).toBeGreaterThan(layout.nameInput!.bottom - 1);
    expect(layout.helper!.height).toBeLessThanOrEqual(22);
    expect(layout.helper!.right).toBeGreaterThanOrEqual(layout.categoryInput!.right - 1);
  } else {
    expect(layout.nameLabel!.y).toBeLessThan(layout.nameInput!.y);
    expect(layout.nameInput!.y).toBeLessThan(layout.helper!.y);
    expect(layout.helper!.y).toBeLessThan(layout.categoryLabel!.y);
    expect(layout.categoryLabel!.y).toBeLessThan(layout.categoryInput!.y);
  }
});

test("業務プロファイルの更新操作はテーブル管理と同じ文言・順序・階層で動作する", async ({ page }) => {
  let profileSearchRequests = 0;
  let schemaRefreshSubmissions = 0;
  let schemaRefreshPolls = 0;
  let dbProfileRefreshSubmissions = 0;
  let dbProfileRefreshPolls = 0;
  let dbProfileRefreshCanComplete = false;

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
  await page.route("**/api/nl2sql/select-ai/db-profiles/refresh-jobs", async (route) => {
    dbProfileRefreshSubmissions += 1;
    await fulfillJson(route, {
      job_id: "profile-db-profile-refresh",
      status: "pending",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: "queued",
      created_at: "2026-07-23T00:00:01Z",
      total_profiles: 0,
      processed_profiles: 0,
      scanned_profiles: 0,
      changed_profiles: 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });
  await page.route("**/api/nl2sql/select-ai/db-profile-refresh-jobs/profile-db-profile-refresh", async (route) => {
    dbProfileRefreshPolls += 1;
    const done = dbProfileRefreshCanComplete && dbProfileRefreshPolls >= 2;
    await fulfillJson(route, {
      job_id: "profile-db-profile-refresh",
      status: done ? "done" : "running",
      mode: "full",
      source: "manual",
      target_profiles: [],
      requires_full_refresh: false,
      phase: done ? "done" : "fetching",
      created_at: "2026-07-23T00:00:01Z",
      started_at: "2026-07-23T00:00:01Z",
      finished_at: done ? "2026-07-23T00:00:02Z" : null,
      total_profiles: 2,
      processed_profiles: done ? 2 : 1,
      scanned_profiles: done ? 2 : 1,
      changed_profiles: done ? 1 : 0,
      deleted_profiles: 0,
      error_code: "",
      error_message: "",
    });
  });

  await page.goto("/profiles");

  await expect(page.getByRole("region", { name: "業務プロファイル状態" })).toHaveCount(0);
  await expect(page.getByText("許可オブジェクト", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Oracle Profile", { exact: true })).toHaveCount(0);

  const actions = page.getByTestId("profile-management-actions");
  const isCompactHeader = (page.viewportSize()?.width ?? 0) < 1024;
  const createButton = actions.getByRole("button", { name: "新規作成", exact: true });
  await expect(createButton).toHaveAttribute("data-page-action-kind", "primary");

  if (isCompactHeader) {
    const moreButton = actions.getByRole("button", { name: "その他の操作", exact: true });
    await expect(actions.getByRole("button")).toHaveText(["新規作成", "その他の操作"]);
    await createButton.focus();
    await page.keyboard.press("Tab");
    await expect(moreButton).toBeFocused();
    await moreButton.press("Enter");

    const menu = page.getByRole("menu");
    await expect(moreButton).toHaveAttribute("aria-expanded", "true");
    await expect(menu.getByRole("menuitem")).toHaveText([
      "表示を更新",
      "DB 構造を再取得",
      "DB Profile 一覧を再取得",
    ]);
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
    await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toHaveCount(0);
    await expect(menu.getByRole("menuitem", { name: "DB 構造を再取得" })).toBeEnabled();

    await menu.getByRole("menuitem", { name: "DB Profile 一覧を再取得" }).click();
    await expect.poll(() => dbProfileRefreshSubmissions).toBe(1);
    const headerStatus = page.locator('header [data-page-header-status="true"]');
    await expect(headerStatus).toContainText(/DB Profile 一覧更新: (待機中|実行中)/);
    await expect(page.getByTestId("profile-management-workspace-processing")).toContainText(
      "DB Profile 一覧を再取得しています"
    );
    await moreButton.click();
    await expect(menu.getByRole("menuitem", { name: "DB Profile 一覧を再取得" })).toBeDisabled();
    await page.keyboard.press("Escape");
    dbProfileRefreshCanComplete = true;
    await expect.poll(() => dbProfileRefreshPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("DB Profile 一覧更新: 完了", { exact: true })).toHaveCount(0);
    await expect(headerStatus).toHaveCount(0);
  } else {
    const actionButtons = actions.getByRole("button");
    const refreshButton = actions.getByRole("button", { name: "表示を更新", exact: true });
    const schemaRefreshButton = actions.getByRole("button", {
      name: "DB 構造を再取得",
      exact: true,
    });
    const dbProfileRefreshButton = actions.getByRole("button", {
      name: "DB Profile 一覧を再取得",
      exact: true,
    });

    await expect(actionButtons).toHaveText([
      "新規作成",
      "表示を更新",
      "DB 構造を再取得",
      "DB Profile 一覧を再取得",
    ]);
    await expect(actions.locator('[data-page-action-group="utility"][data-page-action-group-start="true"]')).toBeVisible();
    await expect(createButton).toHaveClass(/\bbg-primary\b/);
    await expect(refreshButton).toHaveClass(/\bbg-card\b/);
    await expect(schemaRefreshButton).toHaveClass(/\bbg-card\b/);

    const [createBox, refreshBox, schemaRefreshBox, dbProfileRefreshBox] = await Promise.all([
      createButton.boundingBox(),
      refreshButton.boundingBox(),
      schemaRefreshButton.boundingBox(),
      dbProfileRefreshButton.boundingBox(),
    ]);
    expect(createBox).not.toBeNull();
    expect(refreshBox).not.toBeNull();
    expect(schemaRefreshBox).not.toBeNull();
    expect(dbProfileRefreshBox).not.toBeNull();
    const visuallyAfter = (
      previous: NonNullable<typeof createBox>,
      next: NonNullable<typeof createBox>
    ) => next.y > previous.y + previous.height / 2 || next.x > previous.x;
    expect(visuallyAfter(createBox!, refreshBox!)).toBe(true);
    expect(visuallyAfter(refreshBox!, schemaRefreshBox!)).toBe(true);
    expect(visuallyAfter(schemaRefreshBox!, dbProfileRefreshBox!)).toBe(true);

    await createButton.focus();
    await page.keyboard.press("Tab");
    await expect(refreshButton).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(schemaRefreshButton).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(dbProfileRefreshButton).toBeFocused();

    const requestsBeforeRefresh = profileSearchRequests;
    await refreshButton.click();
    await expect.poll(() => profileSearchRequests).toBeGreaterThan(requestsBeforeRefresh);
    expect(schemaRefreshSubmissions).toBe(0);

    await schemaRefreshButton.click();
    await expect.poll(() => schemaRefreshSubmissions).toBe(1);
    await expect(page.getByText("スキーマ更新: 実行中", { exact: true })).toBeVisible();
    await expect(schemaRefreshButton).toBeDisabled();
    await expect.poll(() => schemaRefreshPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toHaveCount(0);
    await expect(schemaRefreshButton).toBeEnabled();

    await dbProfileRefreshButton.click();
    await expect.poll(() => dbProfileRefreshSubmissions).toBe(1);
    const headerStatus = page.locator('header [data-page-header-status="true"]');
    await expect(headerStatus).toContainText(/DB Profile 一覧更新: (待機中|実行中)/);
    await expect(page.getByTestId("profile-management-workspace-processing")).toContainText(
      "DB Profile 一覧を再取得しています"
    );
    await expect(dbProfileRefreshButton).toBeDisabled();
    dbProfileRefreshCanComplete = true;
    await expect.poll(() => dbProfileRefreshPolls).toBeGreaterThanOrEqual(2);
    await expect(page.getByText("DB Profile 一覧更新: 完了", { exact: true })).toHaveCount(0);
    await expect(headerStatus).toHaveCount(0);
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
  await expect(page.getByText("Oracle Profile 反映結果", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("profile-oracle-result")).toHaveCount(0);
  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  const profileRow = page.getByRole("row").filter({ hasText: "既定プロファイル" });
  await profileRow.locator("td").nth(1).click();
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 既定プロファイル" })
  ).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("既定プロファイル");
  await expect(page.getByLabel("Oracle Profile 名")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Drop 実行" })).toHaveCount(0);

  await expect(page.getByText("語彙・few-shot", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "語彙・同義語" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "few-shot 例" })).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "SQL ルール" })).toHaveCount(0);
  await expect(page.locator("#profile-select-ai")).toBeVisible();
  await expect(page.locator("#profile-select-ai-additional-instructions")).toBeVisible();

  // 対象オブジェクト選択はタブなしで常時表示される
  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");
  await expect(page.getByTestId("profile-allowed-table-list-footer")).toContainText("選択");
  await expect(page.getByTestId("profile-allowed-view-list-footer")).toContainText("選択");
  await expect(tableList.getByText("APP.TABLE_01", { exact: true })).toBeVisible();
  await expect(viewList.getByText("APP.VIEW_02", { exact: true })).toBeVisible();
  await expect(tableList.getByText("APP.VIEW_02", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("APP.TABLE_01", { exact: true })).toHaveCount(0);
  await expect(page.getByText("SYS$AUDIT", { exact: true })).toHaveCount(0);
  await expect(page.getByText("V_$SESSION", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("表論理名_01", { exact: true })).toBeVisible();
  await expect(viewList.getByText("ビュー論理名_02", { exact: true })).toBeVisible();
  const appTableBulkActions = tableList.getByTestId("profile-allowed-table-list-app-schema-bulk-actions");
  await expect(appTableBulkActions.getByRole("button", { name: "APP をすべて選択" })).toBeEnabled();
  await expect(appTableBulkActions.getByRole("button", { name: "APP の選択を解除" })).toBeEnabled();
  await expect(appTableBulkActions.getByText("全選択", { exact: true })).toBeVisible();
  await expect(appTableBulkActions.getByText("全解除", { exact: true })).toBeVisible();
  const appViewBulkActions = viewList.getByTestId("profile-allowed-view-list-app-schema-bulk-actions");
  await expect(appViewBulkActions.getByRole("button", { name: "APP をすべて選択" })).toBeEnabled();
  await expect(appViewBulkActions.getByRole("button", { name: "APP の選択を解除" })).toBeEnabled();
  await expect(appViewBulkActions.getByText("全選択", { exact: true })).toBeVisible();
  await expect(appViewBulkActions.getByText("全解除", { exact: true })).toBeVisible();

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
  const nameField = page.getByLabel("名称");
  await nameField.fill("sales_profile");
  await expect(nameField).toHaveValue("SALES_PROFILE");
  await roleField.fill("財務分析向け Oracle SQL アシスタント");
  await instructionsField.fill("日付は DATE 型で返す。");
  // ADMIN_EXECUTE ゲートを満たすと保存ボタンが有効になり、Oracle 反映 job を投入する。
  const saveButton = page.getByRole("button", { name: "保存", exact: true });
  await expect(saveButton).toBeDisabled();
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(saveButton).toBeEnabled();
  await saveButton.click();
  // 業務 Profile 保存後はすぐに解除され、Oracle 反映は polling で完了を受け取る。
  await expect(page.getByTestId("profile-oracle-sync-status")).toContainText("Oracle 反映: 完了");
  await expect(page.getByTestId("profile-oracle-result")).toHaveCount(0);
  const payload = savedPayload as {
    name: string;
    allowed_tables: string[];
    allowed_views: string[];
    select_ai_config: Record<string, unknown>;
  } | null;
  expect(payload?.name).toBe("SALES_PROFILE");
  expect(payload?.allowed_tables).toEqual(["APP.TABLE_01", "APP.TABLE_03"]);
  expect(payload?.allowed_views).toEqual(["APP.VIEW_02", "APP.VIEW_04"]);
  expect(payload).toHaveProperty("sql_rules", []);
  expect(payload?.select_ai_config).toMatchObject({
    profile_name: "SALES_PROFILE",
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
  await profileRow.locator("td").nth(1).click();
  await page.getByLabel("名称").fill("DEFAULT_PROFILE");
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

  const shBulkActions = tableList.getByTestId("profile-allowed-table-list-sh-schema-bulk-actions");
  await expect(shBulkActions.getByRole("button", { name: "SH の選択を解除" })).toBeDisabled();
  await shBulkActions.getByRole("button", { name: "SH をすべて選択" }).click();
  await expect(tableList.getByLabel("SH.ORDERS")).toBeChecked();
  await expect(shBulkActions.getByRole("button", { name: "SH の選択を解除" })).toBeEnabled();
  await shBulkActions.getByRole("button", { name: "SH の選択を解除" }).click();
  await expect(tableList.getByLabel("SH.ORDERS")).not.toBeChecked();
  await shBulkActions.getByRole("button", { name: "SH をすべて選択" }).click();
  await expect(tableList.getByLabel("SH.ORDERS")).toBeChecked();

  await page.getByRole("searchbox", { name: "オブジェクト検索" }).fill("SH.ORDERS");
  await expect(tableList.getByLabel("SH.ORDERS")).toBeVisible();
  await expect(tableList.getByLabel("APP.ORDERS")).toHaveCount(0);
});

test("$ と NL2SQL_ のシステム object は業務プロファイルの対象オブジェクトに表示しない", async ({ page }) => {
  const catalog = {
    ...schemaCatalog,
    tables: [
      {
        table_name: "ORDERS",
        qualified_name: "APP.ORDERS",
        logical_name: "受注",
        owner: "APP",
        table_type: "TABLE",
        comment: "業務受注",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "V_ORDERS",
        qualified_name: "APP.V_ORDERS",
        logical_name: "受注ビュー",
        owner: "APP",
        table_type: "VIEW",
        comment: "業務受注ビュー",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "NL2SQL_SCHEMA_OBJECTS",
        qualified_name: "APP.NL2SQL_SCHEMA_OBJECTS",
        logical_name: "NL2SQL schema objects",
        owner: "APP",
        table_type: "TABLE",
        comment: "system table",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "NL2SQL_SYSTEM_VIEW",
        qualified_name: "APP.NL2SQL_SYSTEM_VIEW",
        logical_name: "NL2SQL system view",
        owner: "APP",
        table_type: "VIEW",
        comment: "system view",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "ORDERS",
        qualified_name: "NL2SQL_APP.ORDERS",
        logical_name: "NL2SQL owner business table",
        owner: "NL2SQL_APP",
        table_type: "TABLE",
        comment: "business table",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "RC_BACKUP_ARCHIVELOG_DETAILS",
        qualified_name: "RMAN$CATALOG.RC_BACKUP_ARCHIVELOG_DETAILS",
        logical_name: "RMAN backup details",
        owner: "RMAN$CATALOG",
        table_type: "VIEW",
        comment: "system view",
        row_count: null,
        columns: [],
        constraints: [],
      },
      {
        table_name: "RC_ARCHIVED_LOG",
        qualified_name: "RMAN$CATALOG.RC_ARCHIVED_LOG",
        logical_name: "RMAN archived log",
        owner: "RMAN$CATALOG",
        table_type: "TABLE",
        comment: "system table",
        row_count: null,
        columns: [],
        constraints: [],
      },
    ],
  };
  await mockProfileApi(page, {
    catalog,
    profileItems: [
      {
        ...profiles[0],
        allowed_tables: [
          "APP.ORDERS",
          "APP.NL2SQL_SCHEMA_OBJECTS",
          "NL2SQL_APP.ORDERS",
          "RMAN$CATALOG.RC_ARCHIVED_LOG",
        ],
        allowed_views: [
          "APP.V_ORDERS",
          "APP.NL2SQL_SYSTEM_VIEW",
          "RMAN$CATALOG.RC_BACKUP_ARCHIVELOG_DETAILS",
        ],
      },
    ],
  });

  await page.goto("/profiles?profile=default");

  const tableList = page.getByTestId("profile-allowed-table-list");
  const viewList = page.getByTestId("profile-allowed-view-list");
  await expect(tableList.getByLabel("APP.ORDERS", { exact: true })).toBeChecked();
  await expect(tableList.getByLabel("NL2SQL_APP.ORDERS", { exact: true })).toBeChecked();
  await expect(viewList.getByLabel("APP.V_ORDERS", { exact: true })).toBeChecked();
  await expect(tableList.getByText("RMAN$CATALOG")).toHaveCount(0);
  await expect(viewList.getByText("RMAN$CATALOG")).toHaveCount(0);
  await expect(tableList.getByText("APP.NL2SQL_SCHEMA_OBJECTS", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("APP.NL2SQL_SYSTEM_VIEW", { exact: true })).toHaveCount(0);
  await expect(tableList.getByText("RC_ARCHIVED_LOG", { exact: true })).toHaveCount(0);
  await expect(viewList.getByText("RC_BACKUP_ARCHIVELOG_DETAILS", { exact: true })).toHaveCount(0);

  await page.getByRole("searchbox", { name: "オブジェクト検索" }).fill("NL2SQL_SCHEMA");
  await expect(tableList.getByText("選択できるテーブルがありません。")).toBeVisible();
  await expect(viewList.getByText("選択できるビューがありません。")).toBeVisible();

  await page.getByRole("searchbox", { name: "オブジェクト検索" }).fill("RMAN");
  await expect(tableList.getByText("選択できるテーブルがありません。")).toBeVisible();
  await expect(viewList.getByText("選択できるビューがありません。")).toBeVisible();
});

test("Select AI 設定は requested order で並び狭い幅でも重ならない", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles");

  await page.getByRole("button", { name: "新規作成", exact: true }).click();

  await expect(page.getByLabel("Oracle Profile 名")).toHaveCount(0);
  for (const fieldId of [
    "profile-name",
    "profile-category",
    "profile-select-ai-region",
    "profile-select-ai-model",
    "profile-select-ai-max-tokens",
    "profile-select-ai-embedding-model",
  ]) {
    await expect(page.locator(`label[for="${fieldId}"] span[aria-hidden="true"]`)).toHaveText("*");
    await expect(page.locator(`#${fieldId}`)).toHaveAttribute("required", "");
    await expect(page.locator(`#${fieldId}`)).toHaveAttribute("aria-required", "true");
  }
  const region = page.getByLabel("Region");
  const model = page.getByLabel("LLM Model");
  const maxTokens = page.getByLabel("Max Tokens");
  const embeddingModel = page.getByLabel("Embedding Model");
  await expect(region).toBeVisible();
  await expect(model).toBeVisible();
  await expect(maxTokens).toBeVisible();
  await expect(embeddingModel).toBeVisible();
  await expect(maxTokens).toHaveAttribute("min", "4096");
  await expect(maxTokens).toHaveAttribute("max", "32000");
  await expect(maxTokens).toHaveAttribute("step", "1");

  const [regionBox, modelBox, maxTokensBox, embeddingModelBox] = await Promise.all([
    region.boundingBox(),
    model.boundingBox(),
    maxTokens.boundingBox(),
    embeddingModel.boundingBox(),
  ]);
  expect(regionBox).not.toBeNull();
  expect(modelBox).not.toBeNull();
  expect(maxTokensBox).not.toBeNull();
  expect(embeddingModelBox).not.toBeNull();
  const viewportWidth = page.viewportSize()?.width ?? 1280;
  if (viewportWidth >= 768) {
    expect(Math.abs(regionBox!.y - modelBox!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(regionBox!.y - maxTokensBox!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(regionBox!.y - embeddingModelBox!.y)).toBeLessThanOrEqual(1);
    expect(regionBox!.x).toBeLessThan(modelBox!.x);
    expect(modelBox!.x).toBeLessThan(maxTokensBox!.x);
    expect(maxTokensBox!.x).toBeLessThan(embeddingModelBox!.x);
  } else {
    expect(regionBox!.y).toBeLessThanOrEqual(modelBox!.y);
    expect(modelBox!.y).toBeLessThanOrEqual(maxTokensBox!.y);
    expect(maxTokensBox!.y).toBeLessThanOrEqual(embeddingModelBox!.y);
    const bodyWidth = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
  }

  await maxTokens.fill("4095");
  await maxTokens.blur();
  await expect(maxTokens).toHaveValue("4096");
  await maxTokens.fill("32001");
  await maxTokens.blur();
  await expect(maxTokens).toHaveValue("32000");

  await page.setViewportSize({ width: 375, height: 900 });
  const mobileViewport = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(mobileViewport.scrollWidth).toBeLessThanOrEqual(mobileViewport.clientWidth + 1);
});

test("業務プロファイル必須項目は空欄保存を止める", async ({ page }) => {
  await mockProfileApi(page);
  let saveRequests = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/nl2sql/profiles" && request.method() === "POST") {
      saveRequests += 1;
    }
  });
  await page.goto("/profiles");

  await page.getByRole("button", { name: "新規作成", exact: true }).click();
  await page.getByLabel("名称").fill("SALES_PROFILE");
  await page.getByLabel("カテゴリ").fill("販売");
  await page.getByLabel("Region").fill("");
  await page.getByLabel("LLM Model").fill("");
  await page.getByLabel("Embedding Model").fill("");
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByRole("alert").filter({ hasText: "Region を入力してください。" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "LLM Model を入力してください。" })).toBeVisible();
  await expect(page.getByRole("alert").filter({ hasText: "Embedding Model を入力してください。" })).toBeVisible();
  await expect(page.getByLabel("Region")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByLabel("LLM Model")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByLabel("Embedding Model")).toHaveAttribute("aria-invalid", "true");
  expect(saveRequests).toBe(0);

  await page.getByLabel("Region").fill("ap-osaka-1");
  await page.getByLabel("LLM Model").fill("cohere.command-r-plus");
  await page.getByLabel("Embedding Model").fill("cohere.embed-v4.0");
  await expect(page.getByRole("alert").filter({ hasText: "Region を入力してください。" })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "LLM Model を入力してください。" })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "Embedding Model を入力してください。" })).toHaveCount(0);
});

test("名称は英字開始の識別子だけ保存でき小文字は自動大文字化する", async ({ page }) => {
  await mockProfileApi(page);
  await page.goto("/profiles");

  await page.getByRole("button", { name: "新規作成", exact: true }).click();

  // ADMIN_EXECUTE ゲートを満たして保存ボタンを有効化するが、名称は空のまま保存する。
  await page.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  // spec §2 error-placement: 該当欄の直下に role=alert で表示される。
  const fieldError = page.getByRole("alert").filter({ hasText: "名称を入力してください。" });
  await expect(fieldError).toBeVisible();
  const categoryError = page.getByRole("alert").filter({ hasText: "カテゴリを入力してください。" });
  await expect(categoryError).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByLabel("カテゴリ")).toHaveAttribute("aria-invalid", "true");

  await page.getByLabel("名称").fill("新プロファイル");
  await expect(fieldError).toHaveCount(0);
  await page.getByLabel("カテゴリ").fill("販売");
  await expect(categoryError).toHaveCount(0);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const formatError = page
    .getByRole("alert")
    .filter({ hasText: "名称は英字で開始し、英字・数字・アンダースコアのみ使用してください。" });
  await expect(formatError).toBeVisible();

  await page.getByLabel("名称").fill("1PROFILE");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(formatError).toBeVisible();

  await page.getByLabel("名称").fill("sales_profile");
  await expect(page.getByLabel("名称")).toHaveValue("SALES_PROFILE");
  await expect(formatError).toHaveCount(0);
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
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    await fulfillJson(route, {
      draft_markdown: "# Draft",
      published_markdown: "# Published",
      draft_revision: {
        id: "revision-draft-1",
        version: 2,
        status: "draft",
        schema_fingerprint: "fp",
        etag: "draft-etag",
      },
      published_revision: {
        id: "revision-published-1",
        version: 1,
        status: "published",
        schema_fingerprint: "fp",
        etag: "published-etag",
        published_at: "2026-07-12T00:00:20Z",
      },
      draft_etag: "markdown-draft-etag",
      published_at: "2026-07-12T00:00:20Z",
    });
  });
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
          "「TABLE_01」を公開済み Ontology(スキーマ情報)に解決できません。スキーマ情報を更新するか、オブジェクト名(owner 付き)を確認してください。",
        ],
      });
      return;
    }
    await fulfillJson(route, { ...profileOntologyView, warnings_ja: [] });
  });

  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);

  const unresolved = page.getByTestId("profile-ontology-unresolved");
  const playground = page.getByRole("region", { name: "質問の Ontology 接地確認" });
  await expect(unresolved).toBeVisible();
  await expect(playground.getByTestId("profile-ontology-unresolved")).toBeVisible();
  await expect(unresolved.getByText("TABLE_01", { exact: false })).toBeVisible();

  await unresolved.getByRole("button", { name: "DB 構造を再取得" }).click();

  const schemaRefreshProcessing = page.getByTestId("ontology-build-schema-refresh-processing");
  await expect(schemaRefreshProcessing).toBeVisible();
  await expect(schemaRefreshProcessing).toContainText("DB 構造を再取得しています");
  await expect(page.getByText("スキーマ更新: 実行中", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("profile-ontology-unresolved")).toHaveCount(0);
  await expect(schemaRefreshProcessing).toHaveCount(0);
  await expect(page.getByText("DB 構造を再取得しました。")).toBeVisible();
  await expect(page.getByText("スキーマ更新: 完了", { exact: true })).toHaveCount(0);
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("ontology-build-markdown")).toBeVisible();
  await expect(page.getByRole("region", { name: "質問の Ontology 接地確認" })).toBeVisible();
  expect(schemaRefreshed).toBe(true);
  expect(ontologyViewCalls).toBeGreaterThanOrEqual(2);
});

test("Ontology 未公開のとき旧モデル編集は出さず Markdown Draft と接地確認の導線を示す", async ({ page }) => {
  await mockProfileApi(page);
  // 公開済み Ontology が無い状態では warning が返っても Draft 編集面には出さない。
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    await fulfillJson(route, {
      draft_markdown: "# Draft\n\n- TABLE_01",
      published_markdown: "",
      draft_revision: {
        id: "revision-draft-1",
        version: 1,
        status: "draft",
        schema_fingerprint: "fp",
        etag: "draft-etag",
      },
      published_revision: null,
      draft_etag: "markdown-draft-etag",
      published_at: null,
    });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-view", async (route) => {
    await fulfillJson(route, {
      ontology_graph: null,
      warnings_ja: [
        "「TABLE_01」を公開済み Ontology(スキーマ情報)に解決できません。スキーマ情報を更新するか、オブジェクト名(owner 付き)を確認してください。",
      ],
    });
  });

  await page.goto("/ontology-build?profile=default&tab=model");
  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);

  await expect(page.getByTestId("profile-ontology-empty")).toHaveCount(0);
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("ontology-build-markdown")).toBeVisible();
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toBeVisible();
  await expect(page.getByRole("region", { name: "質問の Ontology 接地確認" })).toBeVisible();
  await expect(page.getByText("公開済み Ontology がまだありません")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-unresolved")).toHaveCount(0);
});
