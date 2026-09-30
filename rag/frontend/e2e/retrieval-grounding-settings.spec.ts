import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
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

    await expect(page.getByRole("heading", { name: "検索方法", exact: true, level: 1 })).toBeVisible();
    // 検索モードは 4 択。legacy 複合方法はカードとして出さない。
    await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /ベクトル/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /キーワード/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /グラフ拡張/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /ツリー検索/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /業務厳格/ })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /補正マルチクエリ/ })).toHaveCount(0);
    // 合成トグル群。
    await expect(page.getByRole("switch", { name: "クエリ拡張" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "LLM マルチクエリ生成" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "gap-stop" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "業務適合加重" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "補正検索" })).toBeVisible();
    // 推奨用途チップは英語生トークンではなく日本語 i18n ラベルで表示する。
    await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toHaveAccessibleName(/一般/);
    // 回答エンジンが DocRAG の業務ビューでは使われない欄に説明を出す。入力は残す(#300)。
    await expect(page.getByRole("radiogroup", { name: "検索モード" })).toHaveAccessibleDescription(
      /回答エンジンが DocRAG の業務ビューでは使われません/
    );
    await expect(page.getByRole("group", { name: "検索オプション" })).toHaveAccessibleDescription(
      /回答エンジンが DocRAG の業務ビューでは使われません/
    );
    await expect(page.getByTestId("docrag-unused-note")).toHaveCount(2);
    await expect(page.getByRole("radio", { name: /ベクトル/ })).toBeEnabled();
    // 全文検索の分割方式は 1 つにまとめ、選択を削除した(#588)。
    await expect(page.getByText("全文検索の分割方式")).toHaveCount(0);
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "検索方法" })).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });

  test(`根拠確認設定は処理方式を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await collapseSidebar(page);
    }
    await mockGrounding(page);

    await page.goto("/settings/grounding");

    await expect(page.getByRole("heading", { name: "根拠確認", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("radio", { name: /カスタム/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /フルガバナンス/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /カスタム/ })).toHaveAccessibleName(/高度な設定/);
    await expect(page.getByRole("radio", { name: /リーン/ })).toHaveAccessibleName(/低遅延/);
    await expect(page.getByRole("radio", { name: /フルガバナンス/ })).toHaveAccessibleName(/補正\(CRAG\)/);
    await expect(page.getByRole("radio", { name: /リーン/ })).not.toHaveAccessibleName(/low_latency/);
    // この画面の設定は回答エンジンが DocRAG の業務ビューでは使われない(#300)。
    await expect(page.getByRole("radiogroup", { name: "処理方式" })).toHaveAccessibleDescription(
      /回答エンジンが DocRAG の業務ビューでは使われません/
    );
    await expect(page.getByRole("radio", { name: /リーン/ })).toBeEnabled();
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "根拠確認" })).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索方法の画面で回答の記録の保存期間を保存できる（#593） (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrieval(page);
    let saved: unknown = null;
    await page.route("**/api/settings/answer-records", async (route) => {
      if (route.request().method() === "PATCH") {
        saved = route.request().postDataJSON();
        await route.fulfill({
          json: { data: { retention_days: 0, config_source: "runtime" }, error_messages: [], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json: { data: { retention_days: 90, config_source: "runtime" }, error_messages: [], warning_messages: [] },
      });
    });

    await page.goto("/settings/retrieval");
    const save = page.getByRole("button", { name: "保存期間を保存" });
    await expect(save).toBeDisabled();
    await page.getByRole("combobox", { name: "保存期間", exact: true }).click();
    await page.getByRole("option", { name: "無期限（手動で削除）" }).click();
    await save.click();

    await expect(page.getByText("保存期間を保存しました。")).toBeVisible();
    expect(saved).toEqual({ retention_days: 0 });
    await expect(save).toBeDisabled();
    await expectNoHorizontalOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`検索方法設定に全文検索の分割方式の選択は無い (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route("**/api/settings/retrieval", (route) =>
      route.fulfill({ json: retrievalEnvelope("hybrid_rrf") })
    );

    await page.goto("/settings/retrieval");

    await expect(page.getByRole("switch", { name: "補正検索" })).toBeVisible();
    // 分割は 1 つの方式にまとめ、選択を削除した(#588)。
    await expect(page.getByText("全文検索の分割方式")).toHaveCount(0);
    // 回答の検索と生成などの別のカード（#593）の選択欄は数えない。
    await expect(page.getByRole("combobox", { name: /分割方式/ })).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  test(`検索方法の画面で質問履歴を有効にし、除外する語を保存できる（#593） (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrieval(page);
    let saved: unknown = null;
    const initial = { enabled: false, retention_days: 90, min_count: 3, suggestion_limit: 5, blocklist: [] };
    await page.route("**/api/settings/query-history", async (route) => {
      if (route.request().method() === "PATCH") saved = route.request().postDataJSON();
      await route.fulfill({ json: { data: saved ?? initial, error_messages: [], warning_messages: [] } });
    });

    await page.goto("/settings/retrieval");
    const save = page.getByRole("button", { name: "質問履歴の設定を保存" });
    await expect(save).toBeDisabled();
    await page.getByRole("switch", { name: "質問を保存して候補に使う" }).click();
    await page.getByLabel("保存・表示しない語").fill("給与\n\n 住所 ");
    await save.click();

    await expect(page.getByText("質問履歴の設定を保存しました。")).toBeVisible();
    expect(saved).toEqual({ ...initial, enabled: true, blocklist: ["給与", "住所"] });
    await expect(page.getByRole("switch", { name: "質問を保存して候補に使う" })).toBeChecked();
    await expectNoHorizontalOverflow(page);
  });
}

const ANSWERING_SETTINGS = {
  query_strategy: "auto_routing",
  answer_flow: "crag",
  neighbor_child_count: 3,
  rerank_enabled: true,
  screen_linking_enabled: false,
  config_source: "runtime",
};

async function mockAnsweringSettings(page: Page, saved: unknown[] = []) {
  await page.route("**/api/settings/answering", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      saved.push(body);
      await route.fulfill({
        json: { data: { ...ANSWERING_SETTINGS, ...body }, error_messages: [], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({ json: { data: ANSWERING_SETTINGS, error_messages: [], warning_messages: [] } });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`検索方法の画面で回答の検索と生成の既定を変えて保存できる (#593, ${viewport.name}, ${theme})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      // 外観（テーマ）とサイドバーの折りたたみは共有 UI の ui-store の保存値で決まる。
      await page.addInitScript(
        (state) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state, version: 0 })),
        { theme, sidebarCollapsed: viewport.collapse }
      );
      await mockRetrieval(page);
      const saved: unknown[] = [];
      await mockAnsweringSettings(page, saved);

      await page.goto("/settings/retrieval");
      await expect
        .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
        .toBe(theme === "dark");

      await expect(page.getByText("回答の検索と生成", { exact: true })).toBeVisible();
      // 画面の文言に移植元の呼び名（DocRAG）を出さない（#593）。
      const card = page.getByTestId("answering-settings-card");
      await expect(card).not.toContainText("DocRAG");
      const strategy = page.getByRole("combobox", { name: "質問の拡張", exact: true });
      await expect(strategy).toContainText("自動ルーティング");
      await expect(page.getByRole("combobox", { name: "回答の生成方式", exact: true })).toContainText("補正 RAG");
      await expect(page.getByRole("switch", { name: "Rerank で検索候補を並べ替える" })).toBeChecked();
      const screenLinking = page.getByRole("switch", { name: "画面目録で操作画面を探す" });
      await expect(screenLinking).not.toBeChecked();
      const save = page.getByRole("button", { name: "回答の設定を保存", exact: true });
      await expect(save).toBeDisabled();

      await strategy.click();
      await page.getByRole("option", { name: /RAG フュージョン/ }).click();
      await page.getByRole("combobox", { name: "根拠の前後から加える数", exact: true }).click();
      await page.getByRole("option", { name: "5", exact: true }).click();
      await screenLinking.click();
      await expect(save).toBeEnabled();
      await save.click();

      await expect(page.getByText("回答の検索と生成の設定を保存しました。")).toBeVisible();
      expect(saved).toEqual([
        {
          query_strategy: "rag_fusion",
          answer_flow: "crag",
          neighbor_child_count: 5,
          rerank_enabled: true,
          screen_linking_enabled: true,
        },
      ]);
      await expect(save).toBeDisabled();
      await expect(screenLinking).toBeChecked();
      await expectNoHorizontalOverflow(page);
    });
  }
}

test("回答の検索と生成の既定を読み込めないときは、その欄だけに理由を出す (#593)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockRetrieval(page);
  await page.route("**/api/settings/answering", (route) =>
    route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["読み込めません"], warning_messages: [] },
    })
  );

  await page.goto("/settings/retrieval");

  await expect(page.getByText("回答の検索と生成の設定を読み込めませんでした。")).toBeVisible();
  await expect(page.getByRole("button", { name: "回答の設定を保存" })).toHaveCount(0);
  // 検索方法の本体はそのまま使える。
  await expect(page.getByRole("radio", { name: /ハイブリッド/ })).toBeVisible();
});

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
  await expect(keyword).toBeChecked();
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
  await expect(full).toBeChecked();
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
  // 高しきい値 < 低しきい値 は保存できない。保存を押すと、理由を欄の直下に出してその欄へ移す（#541）。
  await high.fill("0.1");
  const save = page.getByRole("button", { name: "保存" });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(high).toHaveAccessibleDescription(/高しきい値は低しきい値以上の数値を入力してください。/);
  await expect(high).toHaveAttribute("aria-invalid", "true");
  await expect(high).toBeFocused();
  await high.fill("0.8");
  await expect(high).not.toHaveAttribute("aria-invalid", "true");
  await expect(save).toBeEnabled();
  await expect(page.getByRole("switch", { name: "低 grade で回答を保留する" })).toBeVisible();
});

test("検索方法設定の保存に失敗しても未保存の編集を残す (#275)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let patchCount = 0;
  await page.route("**/api/settings/retrieval", async (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1;
      await route.fulfill({
        status: 500,
        json: {
          data: null,
          error_messages: ["検索方法設定を backend/.env へ保存できませんでした。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: retrievalEnvelope("hybrid_rrf") });
  });

  await page.goto("/settings/retrieval");

  const keyword = page.getByRole("radio", { name: /キーワード/ });
  await keyword.click();
  await page.getByRole("switch", { name: "補正検索" }).click();
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("検索方法設定を backend/.env へ保存できませんでした。")).toBeVisible();
  expect(patchCount).toBe(1);
  // 失敗後も利用者の選択を server 値へ戻さず、そのまま再試行できる。
  await expect(keyword).toBeChecked();
  await expect(page.getByRole("switch", { name: "補正検索" })).toBeChecked();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
  await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
});

test("根拠確認のしきい値欄を空にしても 0 を入れず、小数を打ち込める (#275)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockGrounding(page);

  await page.goto("/settings/grounding");

  const low = page.getByRole("spinbutton", { name: "低しきい値" });
  await expect(low).toHaveValue("0.35");
  await low.fill("");
  // 空欄は「CRAG を無効化する 0」として保存させない。保存を押すと欄の直下に理由を出す（#541）。
  await expect(low).toHaveValue("");
  await page.getByRole("button", { name: "保存" }).click();
  await expect(low).toHaveAccessibleDescription(/低しきい値を入力してください。/);
  await expect(low).toBeFocused();
  await low.pressSequentially("0.45");
  await expect(low).toHaveValue("0.45");
  await expect(page.getByRole("button", { name: "保存" })).toBeEnabled();
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
    { name: "reasoning_tree_search", recommended_for: ["manual"] },
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
    { name: "custom", recommended_for: ["advanced", "manual"], dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false, corrective: false },
    { name: "lean", recommended_for: ["low_latency", "simple"], dependency_promotion: false, diversity: false, expansion_mode: "none", compression: false, corrective: false },
    { name: "verified_context", recommended_for: ["general", "balanced"], dependency_promotion: false, diversity: true, expansion_mode: "none", compression: false, corrective: true },
    { name: "context_enrich", recommended_for: ["multi_page", "dependency"], dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: false, corrective: false },
    { name: "compact", recommended_for: ["token_budget", "long_context"], dependency_promotion: false, diversity: true, expansion_mode: "none", compression: true, corrective: false },
    { name: "full_governed", recommended_for: ["compliance", "max_quality"], dependency_promotion: true, diversity: true, expansion_mode: "adaptive", compression: true, corrective: true },
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
