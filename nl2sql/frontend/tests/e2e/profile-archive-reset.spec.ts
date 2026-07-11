import { expect, test, type Page, type Route } from "@playwright/test";

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
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles?include_detail=true", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/profiles**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (route.request().method() === "GET" && path.endsWith("/profiles")) {
      await fulfillJson(route, profiles);
      return;
    }
    const archiveMatch = path.match(/\/profiles\/([^/]+)\/archive$/);
    if (archiveMatch) {
      const id = archiveMatch[1];
      profiles = profiles.map((item) => (item.id === id ? { ...item, archived: true } : item));
      await fulfillJson(route, profiles.find((item) => item.id === id));
      return;
    }
    const restoreMatch = path.match(/\/profiles\/([^/]+)\/restore$/);
    if (restoreMatch) {
      const id = restoreMatch[1];
      profiles = profiles.map((item) => (item.id === id ? { ...item, archived: false } : item));
      await fulfillJson(route, profiles.find((item) => item.id === id));
      return;
    }
    await route.fallback();
  });
}

test("リセットは現在の表示に留まり初期値を復元する", async ({ page }) => {
  await mockProfileManagement(page);
  await page.goto("/profiles");

  const listTab = page.getByRole("tab", { name: "一覧と詳細" });
  const createTab = page.getByRole("tab", { name: "新規作成" });
  const name = page.getByLabel("名称");

  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await name.fill("変更中の名称");
  await page.getByRole("button", { name: "リセット" }).click();
  await expect(name).toHaveValue("経理プロファイル");
  await expect(listTab).toHaveAttribute("aria-selected", "true");

  await createTab.click();
  await name.fill("保存前の新規プロファイル");
  await page.getByRole("button", { name: "リセット" }).click();
  await expect(name).toHaveValue("");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
});

test("アーカイブ済み Profile を同一一覧から復元できる", async ({ page }) => {
  await mockProfileManagement(page);
  await page.goto("/profiles");

  const archiveButton = page.getByRole("button", { name: "アーカイブ", exact: true });
  await archiveButton.click();
  const dialog = page.getByRole("alertdialog", { name: "プロファイルのアーカイブ" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  await archiveButton.click();
  await dialog.getByRole("button", { name: "アーカイブ", exact: true }).click();
  await expect(page.getByText("プロファイルをアーカイブしました。")).toBeVisible();

  const archivedFilter = page.getByRole("button", { name: "アーカイブ済み", exact: true });
  await archivedFilter.click();
  await expect(archivedFilter).toHaveAttribute("aria-pressed", "true");

  const archivedRow = page.getByRole("row").filter({ hasText: "経理プロファイル" });
  await archivedRow.getByRole("button", { name: "詳細" }).click();
  await expect(page.getByLabel("名称")).toBeDisabled();
  await expect(page.getByRole("button", { name: "保存" })).toHaveCount(0);

  await page.getByRole("button", { name: "復元" }).click();
  await expect(page.getByRole("button", { name: "使用中" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("名称")).toHaveValue("経理プロファイル");
  await expect(page.getByText("プロファイルを復元しました。")).toBeVisible();

  const viewport = await page.evaluate(() => ({ body: document.body.scrollWidth, window: window.innerWidth }));
  expect(viewport.body).toBeLessThanOrEqual(viewport.window);
});

test("アーカイブ一覧の読込中と空状態を表示する", async ({ page }) => {
  await mockProfileManagement(page);
  await page.route("**/api/nl2sql/profiles?include_archived=true", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await fulfillJson(route, [profile("accounting", "経理プロファイル")]);
  });

  await page.goto("/profiles");
  await expect(page.getByTestId("profile-list-skeleton")).toBeVisible();
  await page.getByRole("button", { name: "アーカイブ済み", exact: true }).click();
  await expect(page.getByText("アーカイブ済みのプロファイルはありません")).toBeVisible();
});

test("復元 API 失敗時に再試行方法を表示する", async ({ page }) => {
  await mockProfileManagement(page);
  await page.route("**/api/nl2sql/profiles/legacy/restore", async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "復元サービスを利用できません。再読込後に再試行してください。" }),
    });
  });

  await page.goto("/profiles");
  await page.getByRole("button", { name: "アーカイブ済み", exact: true }).click();
  const archivedRow = page.getByRole("row").filter({ hasText: "旧プロファイル" });
  await archivedRow.getByRole("button", { name: "詳細" }).click();
  await page.getByRole("button", { name: "復元" }).click();
  await expect(
    page.getByText("復元サービスを利用できません。再読込後に再試行してください。")
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "復元" })).toBeEnabled();
});
