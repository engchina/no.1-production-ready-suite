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
  profile_name: "NL2SQL_ACCOUNTING_PROFILE",
  region: "ap-osaka-1",
  model: "cohere.command-r-plus",
  embedding_model: "cohere.embed-v4.0",
  max_tokens: 32000,
  enforce_object_list: true,
  comments: true,
  annotations: false,
  constraints: false,
  role: "Oracle SQL アシスタント",
  additional_instructions: "",
};

function profile(id: string, name: string, archived = false) {
  return {
    id,
    name,
    category: "経理",
    description: `${name} の説明`,
    allowed_tables: ["INVOICES"],
    allowed_views: [],
    glossary: {},
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: selectAiConfig,
    archived,
  };
}

async function mockProfileManagement(page: Page) {
  let deleteRequests = 0;
  let profiles = [
    profile("accounting", "経理プロファイル"),
    profile("sales", "営業プロファイル"),
    profile("legacy", "旧プロファイル", true),
  ];

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-07-11T00:00:00Z",
      tables: [
        {
          table_name: "INVOICES",
          logical_name: "請求",
          owner: "APP",
          table_type: "TABLE",
          comment: "",
          row_count: null,
          columns: [],
          constraints: [],
        },
      ],
    })
  );
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "catalog-v1",
      refreshed_at: "2026-07-11T00:00:00Z",
      object_count: 1,
      column_count: 0,
      change_token: 1,
      etag: "catalog-v1",
    })
  );
  await page.route("**/api/schema/objects?**", async (route) => {
    const objectType = new URL(route.request().url()).searchParams.get("type") ?? "";
    const items =
      objectType.toUpperCase() === "VIEW"
        ? []
        : [
            {
              owner: "APP",
              object_name: "INVOICES",
              object_type: "TABLE",
              logical_name: "請求",
              comment: "",
              row_count: null,
              column_count: 0,
              last_ddl_at: "2026-07-11T00:00:00Z",
            },
          ];
    await fulfillJson(route, {
      items,
      next_cursor: null,
      total: items.length,
      catalog_version: 1,
    });
  });
  await page.route("**/api/schema/owners", (route) =>
    fulfillJson(route, {
      current_owner: "APP",
      owners: [{ owner: "APP", is_current: true, table_count: 1, view_count: 0 }],
      excluded_oracle_maintained_count: 0,
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route(
    "**/api/nl2sql/select-ai/db-profiles?include_detail=true&business_profiles_only=true&include_archived_business_profiles=true",
    (route) => fulfillJson(route, { runtime: "deterministic", profiles: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles?include_detail=true", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [], warnings: [] })
  );
  // 編集画面マウント時の ontology 系 GET を高速 mock(未 mock だと dev proxy の
  // 失敗待ちが発生し、負荷時に flake の原因になる)
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) => fulfillJson(route, {}));
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) =>
    fulfillJson(route, { proposals: [] })
  );
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, { revisions: [], active_revision_id: "" })
  );
  await page.route("**/api/nl2sql/profiles", async (route) => {
    if (route.request().method() === "GET") {
      await fulfillJson(route, profiles);
      return;
    }
    await route.fallback();
  });
  await page.route("**/api/nl2sql/profiles/*", async (route) => {
    const url = new URL(route.request().url());
    const profileId = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    if (route.request().method() === "GET" && profileId === "search") {
      const items = profiles
        .filter((item) => !item.archived)
        .map((item) => ({
          id: item.id,
          name: item.name,
          category: item.category,
          description: item.description,
          archived: item.archived,
          allowed_table_count: item.allowed_tables.length,
          allowed_view_count: item.allowed_views.length,
          glossary_count: Object.keys(item.glossary).length,
          few_shot_count: item.few_shot_examples.length,
          version: 1,
          etag: `profile-${item.id}-v1`,
          updated_at: "2026-07-11T00:00:00Z",
        }));
      await fulfillJson(route, { items, next_cursor: null, total: items.length, change_token: 1 });
      return;
    }
    if (route.request().method() === "GET") {
      const target = profiles.find((item) => item.id === profileId);
      if (target) {
        await fulfillJson(route, { ...target, version: 1, etag: `profile-${target.id}-v1` });
      } else {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ detail: "指定された profile が見つかりません。" }),
        });
      }
      return;
    }
    if (route.request().method() !== "DELETE") {
      await route.fallback();
      return;
    }
    deleteRequests += 1;
    const target = profiles.find((item) => item.id === profileId);
    if (!target) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "指定された profile が見つかりません。" }),
      });
      return;
    }
    profiles = profiles.filter((item) => item.id !== profileId);
    await fulfillJson(route, target);
  });
  return { deleteRequests: () => deleteRequests };
}

test("一覧の編集ボタンでエディタを開き、一覧に戻るで戻れる", async ({ page }) => {
  await mockProfileManagement(page);
  await page.goto("/profiles");

  const listPanel = page.locator("#profile-management-panel-list");

  await expect(page.getByRole("tab", { name: "一覧", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "新規作成/編集" })).toHaveCount(0);
  await expect(page.getByTestId("fixed-split-pane-profile-management-list")).toHaveCount(0);
  await expect(listPanel.getByRole("heading", { name: "プロファイル" })).toBeVisible();
  await expect(listPanel.getByRole("heading", { name: /プロファイル編集/ })).toHaveCount(0);

  const salesRow = page.getByRole("row").filter({ hasText: "営業プロファイル" });
  await salesRow.getByRole("button", { name: "編集", exact: true }).click();
  await expect(page).toHaveURL(/\/profiles\?profile=sales$/);
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 営業プロファイル" })
  ).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("営業プロファイル");
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  await expect(page).not.toHaveURL(/profile=/);
  await expect(listPanel.getByRole("heading", { name: "プロファイル" })).toBeVisible();
  await page.getByRole("button", { name: /^経理プロファイル/ }).click();
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 経理プロファイル" })
  ).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("経理プロファイル");
});

test("URL 深リンクでエディタを直接開ける", async ({ page }) => {
  await mockProfileManagement(page);

  await page.goto("/profiles?profile=sales");
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 営業プロファイル" })
  ).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("営業プロファイル");

  await page.goto("/profiles?profile=new");
  await expect(page.getByRole("heading", { name: "新規プロファイル" })).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("");

  await page.goto("/profiles?profile=missing");
  await expect(
    page.locator("#profile-management-panel-list").getByRole("heading", { name: "プロファイル" })
  ).toBeVisible();
  await expect(page).not.toHaveURL(/profile=/);
});

test("未保存の変更があるときは一覧に戻る前に確認する", async ({ page }) => {
  await mockProfileManagement(page);
  await page.goto("/profiles?profile=sales");
  await expect(page.getByLabel("名称")).toHaveValue("営業プロファイル");

  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toHaveCount(0);
  await expect(page.locator("#profile-management-panel-list")).toBeVisible();

  await page
    .getByRole("row")
    .filter({ hasText: "営業プロファイル" })
    .getByRole("button", { name: "編集", exact: true })
    .click();
  await page.getByLabel("名称").fill("営業プロファイル改");
  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: "変更を破棄しますか" });
  await expect(dialog.getByText("保存されていない変更があります。一覧に戻ると破棄されます。")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  await expect(page.getByLabel("名称")).toHaveValue("営業プロファイル改");

  await page.getByRole("button", { name: "一覧に戻る", exact: true }).click();
  await page
    .getByRole("alertdialog", { name: "変更を破棄しますか" })
    .getByRole("button", { name: "破棄して戻る", exact: true })
    .click();
  await expect(page.locator("#profile-management-panel-list")).toBeVisible();
  await expect(page).not.toHaveURL(/profile=/);
});

test("リセットとアーカイブ関連 UI は表示しない", async ({ page }) => {
  await mockProfileManagement(page);
  await page.goto("/profiles");

  const main = page.locator("main");
  await expect(main.getByRole("button", { name: "リセット", exact: true })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "アーカイブ", exact: true })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "使用中", exact: true })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "アーカイブ済み", exact: true })).toHaveCount(0);
  await expect(main.getByText("旧プロファイル")).toHaveCount(0);

  await page.getByRole("button", { name: "新規作成", exact: true }).click();
  await expect(page.getByRole("heading", { name: "新規プロファイル" })).toBeVisible();
  await expect(page.getByLabel("名称")).toHaveValue("");
  await expect(main.getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await expect(main.getByRole("button", { name: "リセット", exact: true })).toHaveCount(0);
  await expect(main.getByRole("button", { name: "アーカイブ", exact: true })).toHaveCount(0);

  const viewport = await page.evaluate(() => ({ body: document.body.scrollWidth, window: window.innerWidth }));
  expect(viewport.body).toBeLessThanOrEqual(viewport.window);
});

test("一覧と編集画面からプロファイルを確認付きで削除できる", async ({ page }) => {
  const api = await mockProfileManagement(page);
  await page.goto("/profiles");

  const salesRow = page.getByRole("row").filter({ hasText: "営業プロファイル" });
  await salesRow.getByRole("button", { name: "削除", exact: true }).click();
  const dialog = page.getByRole("alertdialog", { name: "プロファイルを削除しますか" });
  await expect(dialog.getByText("プロファイルを削除しますか")).toBeVisible();
  await expect(dialog.getByText("「営業プロファイル」を業務プロファイル一覧から完全に削除します。")).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル", exact: true }).click();
  expect(api.deleteRequests()).toBe(0);
  await expect(page.getByText("営業プロファイル")).toBeVisible();

  await salesRow.getByRole("button", { name: "削除", exact: true }).click();
  await page
    .getByRole("alertdialog", { name: "プロファイルを削除しますか" })
    .getByRole("button", { name: "削除", exact: true })
    .click();
  expect(api.deleteRequests()).toBe(1);
  await expect(page.getByText("「営業プロファイル」を削除しました。")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "営業プロファイル" })).toHaveCount(0);

  const accountingRow = page.getByRole("row").filter({ hasText: "経理プロファイル" });
  await accountingRow.getByRole("button", { name: "編集", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "プロファイル編集: 経理プロファイル" })
  ).toBeVisible();
  await page.getByRole("button", { name: "削除", exact: true }).click();
  await page
    .getByRole("alertdialog", { name: "プロファイルを削除しますか" })
    .getByRole("button", { name: "削除", exact: true })
    .click();
  expect(api.deleteRequests()).toBe(2);
  await expect(
    page.locator("#profile-management-panel-list").getByRole("heading", { name: "プロファイル" })
  ).toBeVisible();
  await expect(page.getByText("「経理プロファイル」を削除しました。")).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "経理プロファイル" })).toHaveCount(0);

  await page.setViewportSize({ width: 375, height: 900 });
  const bodyWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(bodyWidth.scrollWidth).toBeLessThanOrEqual(bodyWidth.clientWidth + 1);
});
