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
  test(`Vector Index 設定は精度プロファイルを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockVectorIndex(page, "balanced");

    await page.goto("/settings/vector-index");

    await expect(page.getByRole("heading", { name: "索引/検索精度プロファイル" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /バランス/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高精度/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /高速/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "Vector Index アダプター" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("Vector Index 設定は accurate 選択で再プロビジョニング警告を出して保存できる", async ({
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
  await expect(page.getByText("再プロビジョニング", { exact: false }).first()).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("索引/検索精度を保存しました。")).toBeVisible();
  expect(saved).toEqual({ profile: "accurate" });
  await expectNoHorizontalOverflow(page);
});

test("Vector Index 設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/vector-index", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["索引/検索精度設定を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/vector-index");

  await expect(page.getByRole("alert")).toContainText("索引/検索精度設定を取得できませんでした。");
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
