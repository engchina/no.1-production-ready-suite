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
  test(`検索方法設定は検索モードとオプションを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await collapseSidebar(page);
    }
    await mockRetrieval(page);

    await page.goto("/settings/retrieval");

    await expect(page.getByRole("heading", { name: "検索方法" })).toBeVisible();
    // 検索モードは 4 択。legacy 複合方法はカードとして出さない。
    await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /ベクトル/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /キーワード/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /グラフ拡張/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /業務厳格/ })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /補正マルチクエリ/ })).toHaveCount(0);
    // 合成トグル群。
    await expect(page.getByRole("switch", { name: "クエリ拡張" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "LLM マルチクエリ生成" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "gap-stop" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "業務適合加重" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "補正検索" })).toBeVisible();
    // 推奨用途チップは英語生トークンではなく日本語 i18n ラベルで表示する。
    await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toContainText("一般");
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
    await expect(page.getByRole("radio", { name: /フルガバナンス/ })).toContainText("補正(CRAG)");
    await expect(page.getByRole("link", { name: "根拠確認" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoHorizontalOverflow(page);
  });
}

test("検索方法設定はモードとトグルを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/retrieval", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: retrievalEnvelope("keyword", { corrective_retrieval: true }),
      });
      return;
    }
    await route.fulfill({ json: retrievalEnvelope("hybrid_rrf") });
  });

  await page.goto("/settings/retrieval");

  const keyword = page.getByRole("radio", { name: /キーワード/ });
  await keyword.click();
  await expect(keyword).toHaveAttribute("aria-checked", "true");
  await page.getByRole("switch", { name: "補正検索" }).click();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("検索方法を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    mode: "keyword",
    query_expansion: true,
    query_expansion_llm: false,
    gap_stop: false,
    corrective_retrieval: true,
    business_fit_weighting: false,
  });
  await expectNoHorizontalOverflow(page);
});

test("LLM マルチクエリ生成はクエリ拡張 OFF で無効化される", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockRetrieval(page);

  await page.goto("/settings/retrieval");

  const llmSwitch = page.getByRole("switch", { name: "LLM マルチクエリ生成" });
  await expect(llmSwitch).toBeEnabled();
  await page.getByRole("switch", { name: "クエリ拡張" }).click();
  await expect(llmSwitch).toBeDisabled();
});

test("legacy 設定は読み替え notice とトグル ON で表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/retrieval", async (route) => {
    await route.fulfill({
      json: retrievalEnvelope("hybrid_rrf", {
        legacy_strategy: "business_context_strict",
        gap_stop: true,
        business_fit_weighting: true,
      }),
    });
  });

  await page.goto("/settings/retrieval");

  await expect(page.getByText(/旧形式の設定から読み替えて表示しています/)).toBeVisible();
  await expect(page.getByRole("switch", { name: "gap-stop" })).toBeChecked();
  await expect(page.getByRole("switch", { name: "業務適合加重" })).toBeChecked();
  // legacy 読み替え中は同値でも保存できる(保存で新形式へ移行)。
  await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
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
  expect(savedPayload).toEqual({
    pipeline: "full_governed",
    crag_low_confidence_threshold: 0.35,
    crag_high_confidence_threshold: 0.7,
    crag_max_hops: 1,
    crag_low_evidence_abstain: false,
  });
  await expectNoHorizontalOverflow(page);
});

test("根拠確認設定は CRAG しきい値を編集・検証できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockGrounding(page);

  await page.goto("/settings/grounding");

  await expect(page.getByText("補正検索(CRAG)のしきい値")).toBeVisible();
  const high = page.getByRole("spinbutton", { name: "高しきい値" });
  await expect(high).toHaveValue("0.7");
  // 高しきい値 < 低しきい値 は保存できない。
  await high.fill("0.1");
  await expect(
    page.getByText(/高しきい値は低しきい値以上/)
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "保存" })).toBeDisabled();
  await high.fill("0.8");
  await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
  await expect(page.getByRole("switch", { name: "低 grade で回答を保留する" })).toBeVisible();
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

function retrievalEnvelope(
  mode: string,
  overrides: Partial<{
    legacy_strategy: string | null;
    query_expansion: boolean;
    query_expansion_llm: boolean;
    gap_stop: boolean;
    corrective_retrieval: boolean;
    business_fit_weighting: boolean;
  }> = {}
) {
  const modeSpecs = [
    { name: "hybrid_rrf", recommended_for: ["general"] },
    { name: "vector", recommended_for: ["semantic"] },
    { name: "keyword", recommended_for: ["named_entity"] },
    { name: "graph_augmented", recommended_for: ["relationship"] },
  ];
  const statuses = modeSpecs.map((spec) => ({
    ...spec,
    origin: "x",
    selected: spec.name === mode,
    gap_stop: false,
    corrective_retrieval: false,
    business_fit_weighting: false,
  }));
  return {
    data: {
      mode,
      legacy_strategy: null,
      query_expansion: true,
      query_expansion_llm: false,
      gap_stop: false,
      corrective_retrieval: false,
      business_fit_weighting: false,
      modes: statuses,
      config_source: "runtime",
      ...overrides,
    },
    error_messages: [],
    warning_messages: [],
  };
}

function groundingEnvelope(pipeline: string) {
  const specs = [
    { name: "custom", dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false, corrective: false },
    { name: "lean", dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false, corrective: false },
    { name: "verified_context", dependency_promotion: false, diversity: true, expansion_mode: "none", compression: false, corrective: true },
    { name: "context_enrich", dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: false, corrective: false },
    { name: "compact", dependency_promotion: false, diversity: true, expansion_mode: "none", compression: true, corrective: false },
    { name: "full_governed", dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: true, corrective: true },
  ];
  const selected = specs.find((spec) => spec.name === pipeline) ?? specs[0];
  return {
    data: {
      pipeline,
      dependency_promotion_enabled: selected.dependency_promotion,
      diversity_enabled: selected.diversity,
      expansion_mode: selected.expansion_mode,
      compression_enabled: selected.compression,
      crag_low_confidence_threshold: 0.35,
      crag_high_confidence_threshold: 0.7,
      crag_max_hops: 1,
      crag_low_evidence_abstain: false,
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
