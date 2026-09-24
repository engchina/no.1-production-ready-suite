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
  test(`関係情報の構築設定は構築方式を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockGraph(page, "off");

    await page.goto("/settings/graph");

    await expect(page.getByRole("heading", { name: "関係情報の構築" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /構築しない/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /軽量/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /フル/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "関係検索" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("関係情報の構築設定は full を選んで保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let saved: unknown = null;
  await page.route("**/api/settings/graph", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: graphEnvelope("full") });
      return;
    }
    await route.fulfill({ json: graphEnvelope("off") });
  });

  await page.goto("/settings/graph");

  const full = page.getByRole("radio", { name: /フル/ });
  await full.click();
  await expect(full).toHaveAttribute("aria-checked", "true");

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("関係情報の構築設定を保存しました。")).toBeVisible();
  expect(saved).toEqual({ profile: "full" });
  await expectNoHorizontalOverflow(page);
});

test("関係情報の構築設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/graph", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["関係情報の構築設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/graph");

  await expect(page.getByRole("alert")).toContainText("関係情報の構築設定を取得できませんでした。");
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

function graphEnvelope(profile: string) {
  const specs = [
    { name: "off", enabled: false, build_claims: false, build_community_summaries: false },
    { name: "entities", enabled: true, build_claims: false, build_community_summaries: false },
    { name: "full", enabled: true, build_claims: true, build_community_summaries: true },
  ];
  const selected = specs.find((s) => s.name === profile) ?? specs[0];
  return {
    data: {
      profile,
      enabled: selected.enabled,
      build_claims: selected.build_claims,
      build_community_summaries: selected.build_community_summaries,
      profiles: specs.map((s) => ({
        ...s,
        origin: "x",
        recommended_for: ["general"],
        selected: s.name === profile,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockGraph(page: Page, profile: string) {
  await page.route("**/api/settings/graph", async (route) => {
    await route.fulfill({ json: graphEnvelope(profile) });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
