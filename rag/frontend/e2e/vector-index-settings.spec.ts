import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
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

    await expect(page.getByRole("heading", { name: "検索インデックス", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("radio", { name: /バランス/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高精度/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高速/ })).toBeVisible();
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "検索インデックス" })).toHaveAttribute("aria-current", "page");
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
  await expect(accurate).toBeChecked();
  // 実際の索引(32/500)と違う検索精度を選ぶと出る再作成警告(reprovision)を固有文言で検証する。
  await expect(page.getByText("推奨ビルドパラメータを適用するには", { exact: false })).toBeVisible();

  // 保存前(balanced = 実際の索引と一致)は再作成 SQL パネルは出ない。
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

// 実際の索引が推奨ビルドと一致すると、警告と再作成 SQL を出さない(#562)。
for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`検索インデックス設定は現在の索引が推奨と一致すると再作成を案内しない (${viewport.name}, ${theme})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await setUiState(page, { theme, sidebarCollapsed: viewport.collapse });
      await mockVectorIndex(page, "accurate", [48, 800]);

      await page.goto("/settings/vector-index");

      await expect(page.getByRole("radio", { name: /高精度/ })).toBeChecked();
      const currentIndex = page.getByText("現在の索引", { exact: true }).locator("..");
      await expect(currentIndex).toContainText("NEIGHBORS 48 / EFCONSTRUCTION 800");
      await expect(page.getByText("推奨ビルドパラメータを適用するには", { exact: false })).toHaveCount(0);
      await expect(page.getByRole("heading", { name: "索引再作成 SQL" })).toHaveCount(0);

      // 実際の索引と違う検索精度を選ぶと、backend の判定(reprovision)で警告を出す。
      await page.getByRole("radio", { name: /高速/ }).click();
      await expect(page.getByText("推奨ビルドパラメータを適用するには", { exact: false })).toBeVisible();
      await expectNoHorizontalOverflow(page);
    });
  }
}

test("検索インデックス設定は現在の索引を確認できないとき、その旨と再作成 SQL を出す", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockVectorIndex(page, "accurate", null);

  await page.goto("/settings/vector-index");

  const currentIndex = page.getByText("現在の索引", { exact: true }).locator("..");
  await expect(currentIndex).toContainText("確認できません");
  await expect(page.getByText("再作成が必要かを判断できません", { exact: false })).toBeVisible();
  await expect(page.getByText("推奨ビルドパラメータを適用するには", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "索引再作成 SQL" })).toBeVisible();
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
  await expect(fast).toBeChecked();

  // window focus を起点に TanStack Query の再取得を誘発しても未保存選択は維持される。
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(200);

  await expect(fast).toBeChecked();
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
  await setUiState(page, { sidebarCollapsed: true });
}

async function setUiState(
  page: Page,
  state: { theme?: "light" | "dark"; sidebarCollapsed?: boolean }
) {
  // 外観の選好とサイドナビの折りたたみ(共有 UI の ui-store が保存する値)を読み込みの前に入れる。
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: value, version: 0 }));
  }, state);
}

// actual は実際の索引の [NEIGHBORS, EFCONSTRUCTION]。null は確認できない(#562)。
function vectorIndexEnvelope(profile: string, actual: [number, number] | null = [32, 500]) {
  const specs = [
    { name: "balanced", target_accuracy: 95, neighbors: 32, efconstruction: 500 },
    { name: "accurate", target_accuracy: 98, neighbors: 48, efconstruction: 800 },
    { name: "fast", target_accuracy: 85, neighbors: 16, efconstruction: 300 },
  ];
  const selected = specs.find((s) => s.name === profile) ?? specs[0];
  const statusOf = (spec: { neighbors: number; efconstruction: number }) =>
    actual === null
      ? "unknown"
      : actual[0] === spec.neighbors && actual[1] === spec.efconstruction
        ? "match"
        : "reprovision";
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
      requires_reprovision: statusOf(selected) === "reprovision",
      index_status: statusOf(selected),
      actual_neighbors: actual?.[0] ?? null,
      actual_efconstruction: actual?.[1] ?? null,
      profiles: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        distance: "COSINE",
        selected: s.name === profile,
        index_status: statusOf(s),
      })),
      reindex_sql: reindexSql,
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockVectorIndex(
  page: Page,
  profile: string,
  actual: [number, number] | null = [32, 500]
) {
  await page.route("**/api/settings/vector-index", async (route) => {
    await route.fulfill({ json: vectorIndexEnvelope(profile, actual) });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
