import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`検索方法設定は検索方法を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await collapseSidebar(page);
    }
    await mockRetrieval(page);

    await page.goto("/settings/retrieval");

    await expect(page.getByRole("heading", { name: "検索方法" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /業務厳格/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /補正マルチクエリ/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "検索方法" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });

  test(`根拠確認設定は処理方式を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await collapseSidebar(page);
    }
    await mockGrounding(page);

    await page.goto("/settings/grounding");

    await expect(page.getByRole("heading", { name: "根拠確認" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /カスタム/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /フルガバナンス/ })).toBeVisible();
    await expect(page.getByRole("link", { name: "根拠確認" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("検索方法設定は検索方法を保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/retrieval", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({ json: retrievalEnvelope("business_context_strict") });
      return;
    }
    await route.fulfill({ json: retrievalEnvelope("hybrid_rrf") });
  });

  await page.goto("/settings/retrieval");

  const strict = page.getByRole("radio", { name: /業務厳格/ });
  await strict.click();
  await expect(strict).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("検索方法を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({ strategy: "business_context_strict" });
  await expectNoHorizontalOverflow(page);
});

test("根拠確認設定は処理方式を保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/grounding", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({ json: groundingEnvelope("full_governed") });
      return;
    }
    await route.fulfill({ json: groundingEnvelope("custom") });
  });

  await page.goto("/settings/grounding");

  const full = page.getByRole("radio", { name: /フルガバナンス/ });
  await full.click();
  await expect(full).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("根拠確認設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({ pipeline: "full_governed" });
  await expectNoHorizontalOverflow(page);
});

test("検索方法設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/retrieval", async (route) => {
    await route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["検索方法設定を取得できませんでした。"], warning_messages: [] },
    });
  });

  await page.goto("/settings/retrieval");

  await expect(page.getByRole("alert")).toContainText("検索方法設定を取得できませんでした。");
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

function retrievalEnvelope(strategy: string) {
  const specs = [
    { name: "hybrid_rrf", recommended_for: ["general"], gap_stop: false, corrective_retrieval: false, business_fit_weighting: false },
    { name: "vector", recommended_for: ["semantic"], gap_stop: false, corrective_retrieval: false, business_fit_weighting: false },
    { name: "keyword", recommended_for: ["named_entity"], gap_stop: false, corrective_retrieval: false, business_fit_weighting: false },
    { name: "graph_augmented", recommended_for: ["relationship"], gap_stop: false, corrective_retrieval: false, business_fit_weighting: false },
    { name: "business_context_strict", recommended_for: ["compliance"], gap_stop: true, corrective_retrieval: false, business_fit_weighting: true },
    { name: "corrective_multi_query", recommended_for: ["recall_critical"], gap_stop: false, corrective_retrieval: true, business_fit_weighting: false },
  ];
  return {
    data: {
      strategy,
      query_expansion: true,
      gap_stop: strategy === "business_context_strict",
      corrective_retrieval: strategy === "corrective_multi_query",
      business_fit_weighting: strategy === "business_context_strict",
      strategies: specs.map((spec) => ({ ...spec, origin: "x", selected: spec.name === strategy })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

function groundingEnvelope(pipeline: string) {
  const specs = [
    { name: "custom", dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false },
    { name: "lean", dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false },
    { name: "verified_context", dependency_promotion: false, diversity: true, expansion_mode: "none", compression: false },
    { name: "context_enrich", dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: false },
    { name: "compact", dependency_promotion: false, diversity: true, expansion_mode: "none", compression: true },
    { name: "full_governed", dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: true },
  ];
  const selected = specs.find((spec) => spec.name === pipeline) ?? specs[0];
  return {
    data: {
      pipeline,
      dependency_promotion_enabled: selected.dependency_promotion,
      diversity_enabled: selected.diversity,
      expansion_mode: selected.expansion_mode,
      compression_enabled: selected.compression,
      pipelines: specs.map((spec) => ({
        ...spec,
        origin: "x",
        recommended_for: ["general"],
        selected: spec.name === pipeline,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockRetrieval(page: Page) {
  await page.route("**/api/settings/retrieval", async (route) => {
    await route.fulfill({ json: retrievalEnvelope("hybrid_rrf") });
  });
}

async function mockGrounding(page: Page) {
  await page.route("**/api/settings/grounding", async (route) => {
    await route.fulfill({ json: groundingEnvelope("custom") });
  });
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
