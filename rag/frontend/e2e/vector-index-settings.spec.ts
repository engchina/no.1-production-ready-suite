import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索インデックス設定は検索精度を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockVectorIndex(page, "balanced");

    await page.goto("/settings/vector-index");

    await expect(page.getByRole("heading", { name: "検索インデックス" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /バランス/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高精度/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高速/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "検索インデックス" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("検索インデックス設定は accurate 選択で索引再作成警告を出して保存できる", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/vector-index", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: vectorIndexEnvelope("accurate") });
      return;
    }
    await route.fulfill({ json: vectorIndexEnvelope("balanced") });
  });

  await page.goto("/settings/vector-index");

  const accurate = page.getByRole("radio", { name: /高精度/ });
  await accurate.click();
  await expect(accurate).toHaveAttribute("aria-checked", "true");
  // 非 balanced 選択時のみ出る再作成警告(reprovision)を固有文言で検証する。
  await expect(page.getByText("推奨ビルドパラメータを適用するには", { exact: false })).toBeVisible();

  // 保存前(balanced 取得済み)は再作成 SQL パネルは出ない。
  await expect(page.getByRole("heading", { name: "索引再作成 SQL" })).toHaveCount(0);

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("検索インデックス設定を保存しました。")).toBeVisible();
  expect(saved).toEqual({ profile: "accurate" });

  // 保存後(accurate)は profile 反映の再作成 SQL がコピー可能な形で提示される。
  await expect(page.getByRole("heading", { name: "索引再作成 SQL" })).toBeVisible();
  const sqlBox = page.getByLabel("索引再作成 SQL");
  await expect(sqlBox).toContainText("DROP INDEX rag_chunks_embedding_hnsw_idx;");
  await expect(sqlBox).toContainText("NEIGHBORS 48");
  await expect(sqlBox).toContainText("EFCONSTRUCTION 800");
  await expect(page.getByRole("button", { name: "SQL をコピー" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("検索インデックス設定は未保存選択を裏の再取得で失わない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/vector-index", async (route) => {
    // GET は常に balanced を返す(=外部状態は変わらない)。
    await route.fulfill({ json: vectorIndexEnvelope("balanced") });
  });

  await page.goto("/settings/vector-index");

  const fast = page.getByRole("radio", { name: /高速/ });
  await fast.click();
  await expect(fast).toHaveAttribute("aria-checked", "true");

  // window focus を起点に TanStack Query の再取得を誘発しても未保存選択は維持される。
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(200);

  await expect(fast).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
});

test("検索インデックス設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/vector-index", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["検索インデックス設定を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/vector-index");

  await expect(page.getByRole("alert")).toContainText("検索インデックス設定を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

function vectorIndexEnvelope(profile: string) {
  const specs = [
    { name: "balanced", target_accuracy: 95, neighbors: 32, efconstruction: 500 },
    { name: "accurate", target_accuracy: 98, neighbors: 48, efconstruction: 800 },
    { name: "fast", target_accuracy: 85, neighbors: 16, efconstruction: 300 },
  ];
  const selected = specs.find((s) => s.name === profile) ?? specs[0];
  const reindexSql =
    `DROP INDEX rag_chunks_embedding_hnsw_idx;\n` +
    `CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx\n` +
    `    ON rag_chunks (embedding)\n` +
    `    ORGANIZATION INMEMORY NEIGHBOR GRAPH\n` +
    `    DISTANCE COSINE\n` +
    `    WITH TARGET ACCURACY ${selected.target_accuracy}\n` +
    `    PARAMETERS (\n` +
    `        TYPE HNSW,\n` +
    `        NEIGHBORS ${selected.neighbors},\n` +
    `        EFCONSTRUCTION ${selected.efconstruction}\n` +
    `    );`;
  return {
    data: {
      profile,
      target_accuracy: selected.target_accuracy,
      neighbors: selected.neighbors,
      efconstruction: selected.efconstruction,
      distance: "COSINE",
      requires_reprovision: profile !== "balanced",
      profiles: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        distance: "COSINE",
        selected: s.name === profile,
      })),
      reindex_sql: reindexSql,
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockVectorIndex(page: Page, profile: string) {
  await page.route("**/api/settings/vector-index", async (route) => {
    await route.fulfill({ json: vectorIndexEnvelope(profile) });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
