import { expect, test, type Page } from "./fixtures/test";
import { FLOW_STAGES } from "./_prompt-stage-fixtures";
import { apiEnvelope, expectNoPageOverflow, mockAuthUser, mockLocalAuth, openSidebarNav } from "./_helpers";
import { findLoadingStandardViolations } from "../../../platform/packages/ui/e2e/loading-checks";

// 検索方法の画面。検索・回答の既定、検索関連プロンプトを持つ。
// 以前の検索モード・検索オプションと、根拠確認・回答スタイル・高度な検索の画面は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索方法の画面は回答の設定と検索プロンプトを出す (${viewport.name})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);
    const removedSettingRequests: string[] = [];
    page.on("request", (request) => { if (/\/api\/settings\/(answer-records|query-history)$/.test(request.url())) removedSettingRequests.push(request.method()); });

    await page.goto("/settings/retrieval");

    await expect(page.getByRole("heading", { name: "検索方法", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByTestId("answering-settings-card")).toBeVisible();
    await expect(page.getByText("既定は 60 件です。0 は追加制限なしです。0～2000 件で指定します。検索文・検索経路ごとに取得する候補数の上限です。融合後の合計件数や、回答に使う根拠数とは異なります。", { exact: true })).toBeVisible();
    await page.locator('[id^="answering-retrieval-query-limit-"][id$="-hint"]').screenshot({ path: testInfo.outputPath("retrieval-query-limit-hint.png") });
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存期間を保存" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "質問履歴の設定を保存" })).toHaveCount(0);
    await expect(page.getByText("回答の記録の保存期間", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "保存期間", exact: true })).toHaveCount(0);
    await expect(page.getByText("質問履歴", { exact: true })).toHaveCount(0);
    expect(removedSettingRequests).toEqual([]);
    await page.getByTestId("answer-prompt-stages-card").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("retrieval-without-history-settings.png") });
    // 削除した検索モード・検索オプション（#595）は出さない。
    await expect(page.getByRole("radiogroup", { name: "検索モード" })).toHaveCount(0);
    await expect(page.getByRole("switch", { name: "クエリ拡張" })).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
    // 画面の文言に移植元の呼び名（DocRAG）を出さない。
    await expect(page.locator("main")).not.toContainText("DocRAG");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。削除した画面はナビに出さない。
    const nav = await openSidebarNav(page);
    await expect(nav.getByRole("link", { name: "検索方法" })).toHaveAttribute("aria-current", "page");
    for (const removed of ["根拠確認", "回答スタイル", "高度な検索"]) {
      await expect(nav.getByRole("link", { name: removed })).toHaveCount(0);
    }
    await expectNoPageOverflow(page);
  });

  test(`検索方法の画面の読み込み中は各カードの先頭に経過時間を 1 か所ずつ出し、ページの先頭に重ねない (${viewport.name})`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    // 回答の設定とプロンプトは別々に読む（読めたものから出す）。それぞれの応答を別々に止める。
    let releaseAnswering: () => void = () => undefined;
    let releasePrompts: () => void = () => undefined;
    const answeringGate = new Promise<void>((resolve) => (releaseAnswering = resolve));
    const promptsGate = new Promise<void>((resolve) => (releasePrompts = resolve));
    await page.route("**/api/settings/answering", async (route) => {
      await answeringGate;
      await route.fulfill({ json: apiEnvelope(ANSWERING_SETTINGS) });
    });
    await page.route("**/api/settings/answer-prompts", async (route) => {
      await promptsGate;
      await route.fulfill({ json: apiEnvelope({ prompts: [], stages: FLOW_STAGES }) });
    });

    await page.goto("/settings/retrieval");

    // ページの先頭の読み込み中の表示は無い（カードの表示と重ねない。#4649）。
    await expect(page.getByTestId("settings-retrieval-loading")).toHaveCount(0);
    const answering = page.getByTestId("answering-settings-loading");
    const prompts = page.getByTestId("settings-retrieval-prompts-loading");
    await expect(answering).toContainText("回答の検索と生成の設定を読み込んでいます");
    await expect(prompts).toContainText("プロンプトを読み込んでいます");
    // カードは読み込み中も枠を出し、保存のボタンはまだ出さない（寸法を予約する）。
    await expect(page.getByTestId("answering-settings-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toHaveCount(0);
    // 縦に並ぶ 2 つのカードは別々の領域なので、経過時間は各カードに 1 つずつ（灰色の枠・全幅・Skeleton）。
    await expect(answering.getByRole("timer")).toHaveCount(1);
    await expect(prompts.getByRole("timer")).toHaveCount(1);
    expect(await findLoadingStandardViolations(page, { expectCount: 2, skeleton: true })).toEqual([]);

    // 回答の設定だけ読めたら、プロンプトのカードの表示だけが残る。
    releaseAnswering();
    await expect(answering).toHaveCount(0);
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expect(page.getByRole("timer")).toHaveCount(1);
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    await prompts.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("loading-retrieval-prompts.png") });

    releasePrompts();
    await expect(prompts).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-stages-card")).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test(`削除した設定の画面の URL は検索方法へ移す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);

    for (const removed of ["/settings/grounding", "/settings/generation", "/settings/agentic"]) {
      await page.goto(removed);
      await expect(page).toHaveURL(/\/settings\/retrieval$/);
      await expect(page.getByRole("heading", { name: "検索方法", exact: true, level: 1 })).toBeVisible();
    }
  });
}

test("検索方法の権限が無い利用者は、削除した画面の URL から権限なしの画面へ移る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockAuthUser(page, { permissions: ["menu.chat"] });

  await page.goto("/settings/grounding");

  await expect(page).toHaveURL(/\/forbidden/);
});




const ANSWERING_SETTINGS = {
  hybrid_search_backend: "oracle_native",
  rrf_method_ratio: [1, 1],
  rrf_source_ratio: [7, 3],
  runtime_terms_search_enabled: false,
  runtime_terms_answer_enabled: false,
  runtime_rules_confirmation_enabled: false,
  runtime_rules_answer_enabled: false,
  neighbor_child_count: 3,
  rerank_enabled: true,
  screen_linking_enabled: false,
  auto_field_filter_enabled: false,
  graph_expansion_enabled: false,
  config_source: "runtime",
  entity_expansion_enabled: true,
  entity_expansion_max_chunks: 6,
  section_expansion_enabled: true,
  section_expansion_max_levels: 4,
  section_expansion_input_token_percent: 50,
};

async function mockAnsweringSettings(page: Page, saved: unknown[] = [], initial: Record<string, unknown> = {}) {
  let current = { ...ANSWERING_SETTINGS, ...initial };
  await page.route("**/api/settings/answering", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      saved.push(body);
      current = { ...current, ...body };
      await route.fulfill({
        json: { data: current, error_messages: [], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({ json: { data: current, error_messages: [], warning_messages: [] } });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  for (const theme of ["light", "dark"] as const) {
  }
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
}

test("回答の検索と生成の既定を読み込めないときは、その欄だけに理由を出す (#593)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockRetrievalCards(page);
  await page.route("**/api/settings/answering", (route) =>
    route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["読み込めません"], warning_messages: [] },
    })
  );

  await page.goto("/settings/retrieval");

  // 取得の失敗は API の理由と再試行をそのカードに出す（ApiErrorState。#906 / #1002）。
  const card = page.getByTestId("answering-settings-card");
  await expect(card.getByRole("alert")).toContainText("読み込めません");
  await expect(card.getByRole("button", { name: "再試行" })).toBeVisible();
  await expect(page.getByRole("button", { name: "回答の設定を保存" })).toHaveCount(0);
  // 検索プロンプトの欄はそのまま使える。
  await expect(page.getByRole("button", { name: "保存期間を保存" })).toHaveCount(0);
  await expect(page.getByTestId("answer-prompt-stages-card")).toBeVisible();
});



test("回答の検索と生成の保存に失敗しても入力を残して操作の行に出し、変更を破棄で保存値へ戻せる（#1002）", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockRetrievalCards(page);
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/settings/answering", async (route) => {
    if (route.request().method() === "PATCH") {
      await gate;
      await route.fulfill({
        status: 500,
        json: {
          data: null,
          error_messages: ["回答の検索と生成の設定を backend/.env へ保存できませんでした。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: apiEnvelope(ANSWERING_SETTINGS) });
  });

  await page.goto("/settings/retrieval");
  const actions = page.getByRole("group", { name: "回答の検索と生成の設定の操作" });
  const confirm = page.getByRole("switch", { name: "登録ルールによる確認の使用", exact: true });
  await confirm.click();
  const neighbor = page.getByRole("combobox", { name: "根拠の前後から加える数", exact: true });
  await neighbor.click();
  await page.getByRole("option", { name: "5", exact: true }).click();
  await actions.getByRole("button", { name: "回答の設定を保存" }).click();
  // 保存中は欄を変えられない（保存の後に保存値で作り直すため、保存中の変更は消える）。
  await expect(neighbor).toBeDisabled();
  await expect(confirm).toBeDisabled();
  release();

  await expect(actions).toContainText("回答の検索と生成の設定を backend/.env へ保存できませんでした。");
  await expect(neighbor).toContainText("5");
  await expect(neighbor).toBeEnabled();
  await expect(confirm).toBeChecked();
  await actions.getByRole("button", { name: "変更を破棄" }).click();
  await expect(neighbor).toContainText("3");
  await expect(confirm).not.toBeChecked();
  await expect(actions.getByRole("button", { name: "回答の設定を保存" })).toBeDisabled();
});


async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}



/** 設定とプロンプトの取得（GET）を mock する。`gate` を渡すと、その間は応答を止める（読み込み中の確認）。 */
async function mockRetrievalCards(page: Page, gate?: Promise<void>, stages = FLOW_STAGES) {
  const cards: [string, unknown][] = [
    ["**/api/settings/answer-prompts", { prompts: [], stages }],
    ["**/api/settings/answering", ANSWERING_SETTINGS],
  ];
  for (const [url, data] of cards) {
    await page.route(url, async (route) => {
      if (gate) await gate;
      await route.fulfill({ json: apiEnvelope(data) });
    });
  }
}

for (const width of [1280, 375]) {
  test(`検索プロンプトは処理順・使用条件・読み取り専用で確認できる (${width}px)`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockAuthUser(page, { permissions: ["menu.settings_retrieval"] });
    const displayStages = [{ id: "rule_selection", phase: "question", prompts: [{ id: "system", content: "## Step 1: 選択を確かめる\n合成の条件。\n\n## Step 2: 結果を返す\nJSON のみ。" }] }, ...FLOW_STAGES].map((stage) => stage.id === "history_rewrite" ? {
      ...stage, prompts: [{ id: "system", content: "## 目的と手順\n\n- 合成の対象名を補います。\n- 質問だけを 1 行で返します。" }],
    } : stage);
    await mockRetrievalCards(page, undefined, displayStages);
    await page.goto("/settings/retrieval");
    const prompts = page.getByRole("region", { name: "検索に使うプロンプト", exact: true });
    const card = page.getByTestId("answer-prompt-stages-card");
    await expect(card).toBeVisible();
    const bounds = await card.boundingBox();
    const reference = await page.getByTestId("answering-settings-card").boundingBox();
    expect(Math.abs(bounds!.width-reference!.width)).toBeLessThanOrEqual(1);
    expect(await card.evaluate(node => parseFloat(getComputedStyle(node).borderTopWidth))).toBeGreaterThan(0);

    await expect(prompts.getByRole("heading", { level: 3 })).toHaveText(["質問の解釈", "検索"]);
    const expected = displayStages.filter((stage) => stage.id !== "support_guide_conditions" && ["question", "retrieval"].includes(stage.phase));
    await expect(prompts.locator("details")).toHaveCount(expected.length);
    for (const stage of expected) {
      const panel = prompts.getByTestId(`answer-prompt-stage-${stage.id}`);
      await expect(panel).toContainText("使われる条件:");
      await panel.locator("summary").click();
      const fields = panel.getByRole("textbox");
      expect(await fields.evaluateAll((elements) => elements.map((element) => (element as HTMLTextAreaElement).value))).toEqual(stage.prompts.map((part) => part.content));
      for (const field of await fields.all()) await expect(field).toHaveAttribute("readonly", "");
      await panel.locator("summary").click();
    }
    await expect(prompts.getByRole("button", { name: "プロンプトを保存" })).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-generation")).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-support_guide_conditions")).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-history_rewrite")).toContainText("書き換えを有効にしたとき");
    await expect(prompts.getByTestId("answer-prompt-stage-section_extraction")).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-rule_selection")).toContainText("曖昧な条件は確認に残します");
    const rewrite = prompts.getByTestId("answer-prompt-stage-history_rewrite");
    await rewrite.locator("summary").click();
    await expect(rewrite.getByRole("textbox")).toHaveValue(/\n- .+\n- /);
    await rewrite.getByRole("textbox").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("history-rewrite-bullets.png") });
    await rewrite.locator("summary").click();
    await prompts.getByRole("heading", { level: 2 }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("retrieval-prompts.png") });
    await expectNoPageOverflow(page);
  });

  test(`検索プロンプトの取得失敗は他の設定を妨げず再試行できる (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockRetrievalCards(page);
    let failed = true;
    await page.route("**/api/settings/answer-prompts", (route) => failed
      ? route.fulfill({ status: 503, json: { data: null, error_messages: ["取得できませんでした"], warning_messages: [] } })
      : route.fulfill({ json: apiEnvelope({ prompts: [], stages: FLOW_STAGES }) }));
    await page.goto("/settings/retrieval");
    await expect(page.getByText("プロンプトを読み込めませんでした。", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存期間を保存" })).toHaveCount(0);
    failed = false;
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(page.getByRole("region", { name: "検索に使うプロンプト", exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

 test("根拠の前後から加える数に不要なゼロの説明を表示しない", {tag: "@responsive"}, async ({page}) => {
  await mockLocalAuth(page);
  await mockRetrievalCards(page);
  await page.goto("/settings/retrieval");
  await expect(page.getByText("当たった根拠の前と後から、それぞれ最大でこの数のチャンクを文脈に加えます。同じ親やページの範囲、取得できる根拠、入力上限によって実際の数は減ります。", {exact:true})).toBeVisible();
  await expect(page.getByText("0 は加えません。", {exact:false})).toHaveCount(0);
 });

test("検索方法の指示の編集内容を保存できる", { tag: "@responsive" }, async ({ page }) => {
  await mockRetrievalCards(page);
  const { mockEditableStage } = await import("./_feature-prompt-helpers");
  const requests = await mockEditableStage(page, "field_filter", "question", "system");
  await page.goto("/settings/retrieval");
  const stage = page.getByTestId("answer-prompt-stage-field_filter");
  await stage.locator("summary").click();
  await expect(stage.getByRole("group", { name: "編集内容の操作" }).getByRole("button"))
    .toHaveText(["プロンプトを保存", "変更を破棄", "既定に戻す"]);
  await stage.getByRole("textbox").fill("## Step 1: 合成の検索指示");
  await stage.getByRole("button", { name: "プロンプトを保存" }).click();
  await expect(stage.getByRole("button", { name: "プロンプトを保存" })).toBeDisabled();
  expect(requests).toEqual([{ key: "field_filter.system", method: "PUT", content: "## Step 1: 合成の検索指示" }]);
  await expectNoPageOverflow(page);
});


// 選別は回答の全体設定で、監査・補検索を自動で有効にしない。


for (const width of [1280, 375]) {
  for (const theme of ["light", "dark"] as const) {
    test(`確認と検索の全体スイッチを一緒に保存でき、生成用は独立して保存できる (${width}px, ${theme})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((state) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state, version: 0 })), { theme, sidebarCollapsed: width === 375 });
      await mockRetrievalCards(page);
      const saved: Record<string, unknown>[] = [];
      await mockAnsweringSettings(page, saved);
      await page.goto("/settings/retrieval");
      const search = page.getByRole("switch", { name: "用語・同義語による検索文の追加", exact: true });
      const confirm = page.getByRole("switch", { name: "登録ルールによる確認の使用", exact: true });
      await expect(search).not.toBeChecked();
      await expect(confirm).not.toBeChecked();
      await expect(search).toHaveAccessibleDescription(/原質問.*検索文を追加します/);
      const area = page.getByRole("region", { name: "検索文の作成" });
      const card = page.getByTestId("answering-settings-card");
      await expect(card.getByRole("region", { name: "回答前の確認", exact: true }).getByRole("switch")).toHaveAccessibleName("登録ルールによる確認の使用");
      await expect(page.getByTestId("runtime_rules_confirmation_enabled")).toHaveCount(0);
      await search.click();
      await confirm.click();
      await card.getByRole("button", { name: "変更を破棄", exact: true }).click();
      await expect(search).not.toBeChecked();
      await expect(confirm).not.toBeChecked();
      await search.click();
      await confirm.click();
      await page.getByTestId("answering-settings-card").getByRole("button", { name: "回答の設定を保存", exact: true }).click();
      await expect(page.getByTestId("answering-settings-card").getByRole("button", { name: "回答の設定を保存", exact: true })).not.toHaveAttribute("aria-busy", "true");
      await expect(card.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeDisabled();
      await expect(search).toBeChecked();
      expect(saved.at(-1)).toMatchObject({ runtime_rules_confirmation_enabled: true, runtime_terms_search_enabled: true });
      expect(saved.at(-1)).not.toHaveProperty("runtime_terms_answer_enabled");
      expect(saved.at(-1)).not.toHaveProperty("runtime_rules_answer_enabled");
      await expect(page.getByRole("button", { name: "登録ルールの確認設定を保存", exact: true })).toHaveCount(0);
      await expect(confirm).toBeChecked();
      await area.scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`search-expressions-${width}-${theme}.png`) });
      await expectNoPageOverflow(page);
      await page.goto("/settings/prompts");
      for (const [testId, label, field] of [
        ["answer-term-settings-card", "回答生成での用語・同義語の使用", "runtime_terms_answer_enabled"],
        ["runtime_rules_answer_enabled", "回答生成での登録ルールの使用", "runtime_rules_answer_enabled"],
      ]) {
        const card = page.getByTestId(testId);
        const toggle = card.getByRole("switch", { name: label, exact: true });
        await expect(toggle).not.toBeChecked();
        await toggle.click();
        const save = card.getByRole("button", { name: field === "runtime_terms_answer_enabled" ? "用語・同義語の使用を保存" : "登録ルールの回答設定を保存", exact: true });
        await save.click();
        await expect(save).toBeDisabled();
        await expect(save).not.toHaveAttribute("aria-busy", "true");
        expect(saved.at(-1)).toEqual({ [field]: true });
      }
      await page.getByTestId("answer-term-settings-card").scrollIntoViewIfNeeded();
      await page.screenshot({ path: testInfo.outputPath(`answer-knowledge-${width}-${theme}.png`) });
      await expectNoPageOverflow(page);
      await page.goto("/settings/retrieval");
      await expect(search).toBeChecked();
      await expect(confirm).toBeChecked();
    });
  }
}

for (const theme of ["light", "dark"] as const) {
  test(`RRF の比重を検証して保存・再表示できる (${theme})`, {tag: "@responsive"}, async ({page}, testInfo) => {
    await page.addInitScript((theme) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({state: {theme, sidebarCollapsed: true},version: 0})), theme);
    await mockRetrievalCards(page);
    const saved: unknown[] = [];
    await mockAnsweringSettings(page, saved);
    await page.goto("/settings/retrieval");
    const section = page.locator("section", {has: page.getByRole("heading", {name: "検索結果の統合（RRF）",exact:true})});
    for (const [name,value] of [["ベクトル検索の比重","1"],["全文検索の比重","1"],["原質問の比重","7"],["追加検索文全体の比重","3"]]) {
      await expect(section.getByRole("spinbutton", {name,exact:true})).toHaveValue(value);
    }
    await section.getByRole("spinbutton", {name:"ベクトル検索の比重",exact:true}).fill("0");
    await section.getByRole("spinbutton", {name:"全文検索の比重",exact:true}).fill("0");
    await expect(section.getByRole("alert")).toBeVisible();
    await expect(page.getByRole("button", {name:"回答の設定を保存",exact:true})).toBeDisabled();
    await section.getByRole("spinbutton", {name:"全文検索の比重",exact:true}).fill("3");
    await section.getByRole("spinbutton", {name:"原質問の比重",exact:true}).fill("4");
    await section.screenshot({path:testInfo.outputPath(`rrf-ratios-${theme}.png`)});
    await page.getByRole("button", {name:"回答の設定を保存",exact:true}).click();
    await expect.poll(() => saved.length).toBe(1);
    expect(saved[0]).toMatchObject({rrf_method_ratio:[0,3],rrf_source_ratio:[4,3]});
    await page.reload();
    await expect(page.getByRole("spinbutton", {name:"原質問の比重",exact:true})).toHaveValue("4");
    await expectNoPageOverflow(page);
  });
}


for (const theme of ["light", "dark"] as const) {
  test(`再ランキング後の並びの比率を検証して保存・再表示できる (${theme})`, {tag: "@responsive"}, async ({page}, testInfo) => {
    await page.addInitScript((theme) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({state: {theme, sidebarCollapsed: true},version: 0})), theme);
    await mockRetrievalCards(page);
    const saved: unknown[] = [];
    await mockAnsweringSettings(page, saved, {rerank_slot_ratio: undefined});
    await page.goto("/settings/retrieval");
    const section = page.locator("section", {has: page.getByRole("heading", {name: "検索結果の統合（RRF）",exact:true})});
    const rerank = section.getByRole("spinbutton", {name:"再ランキングの順から取る件数",exact:true});
    const search = section.getByRole("spinbutton", {name:"検索の順から取る件数",exact:true});
    await expect(rerank).toHaveValue("7");
    await expect(search).toHaveValue("3");
    await expect(section).toContainText("Rerank が無効・失敗のときは適用しません");
    await rerank.fill("0");
    await search.fill("0");
    await expect(section.getByRole("alert")).toContainText("少なくとも一方を 1 以上");
    await expect(page.getByRole("button", {name:"回答の設定を保存",exact:true})).toBeDisabled();
    await search.fill("1.5");
    await expect(page.getByRole("button", {name:"回答の設定を保存",exact:true})).toBeDisabled();
    await rerank.fill("4");
    await search.fill("1");
    await expect(section.getByRole("alert")).toHaveCount(0);
    await section.screenshot({path:testInfo.outputPath(`rerank-slot-ratio-${theme}.png`)});
    await page.getByRole("button", {name:"回答の設定を保存",exact:true}).click();
    await expect.poll(() => saved.length).toBe(1);
    expect(saved[0]).toMatchObject({rerank_slot_ratio:[4,1]});
    await page.reload();
    await expect(page.getByRole("spinbutton", {name:"再ランキングの順から取る件数",exact:true})).toHaveValue("4");
    await expectNoPageOverflow(page);
  });
}

for (const theme of ["light", "dark"] as const) {
test(`RRF の比重が未指定の場合も新しい既定値と公式説明を表示する (${theme})`, {tag: "@responsive"}, async ({page}, testInfo) => {
  await page.addInitScript((theme) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({state: {sidebarCollapsed: true, theme},version: 0})), theme);
  await mockRetrievalCards(page);
  await mockAnsweringSettings(page, [], {rrf_method_ratio: undefined, rrf_source_ratio: undefined});
  await page.goto("/settings/retrieval");
  const section = page.locator("section", {has: page.getByRole("heading", {name: "検索結果の統合（RRF）",exact:true})});
  for (const [name,value] of [["ベクトル検索の比重","7"],["全文検索の比重","3"],["原質問の比重","7"],["追加検索文全体の比重","3"]]) {
    await expect(section.getByRole("spinbutton", {name,exact:true})).toHaveValue(value);
  }
  await expect(section).toContainText("本システムの既定はベクトル 7・全文 3");
  await expect(section).toContainText("Oracle 公式 API の既定はベクトル 10・全文 1");
  await expect(section).toContainText("5:1 は計算例");
  await expect(section).toContainText("7:3 は本システム独自の予算");
  for (const [name, href] of [["Oracle 公式：検索 API の既定値", "https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/search.html"], ["Oracle 公式：重み付き RRF の計算", "https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/understand-hybrid-search.html"]]) {
    const link = section.getByRole("link", {name: new RegExp(name)});
    await expect(link).toHaveAttribute("href", href);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  }
  await section.screenshot({path:testInfo.outputPath(`rrf-ratio-defaults-${theme}.png`), animations: "disabled"});
  await expectNoPageOverflow(page);
});

}

for (const theme of ["light", "dark"] as const) {
  test(`類似度と関連度の下限を独立して検証・保存し、失敗時の入力を保持する (${theme})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    const width = page.viewportSize()?.width === 375 ? 375 : 1440;
    await page.setViewportSize({ width, height: 900 });
    await page.addInitScript((state) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state, version: 0 })), { theme, sidebarCollapsed: width === 375 });
    await mockRetrievalCards(page);
    const saved: Record<string, unknown>[] = [];
    await mockAnsweringSettings(page, saved, { min_similarity: 0.18, rerank_min_relevance_score: 0.27 });
    let fail = false;
    await page.route("**/api/settings/answering", (route) => fail && route.request().method() === "PATCH"
      ? route.fulfill({ status: 503, json: { data: null, error_messages: ["設定を保存できません。"], warning_messages: [] } })
      : route.fallback());
    await page.goto("/settings/retrieval");
    const vector = page.getByRole("spinbutton", { name: "コサイン類似度の下限", exact: true });
    const rerank = page.getByRole("spinbutton", { name: "再ランキングの関連度スコア下限", exact: true });
    const save = page.getByRole("button", { name: "回答の設定を保存", exact: true });
    await expect(vector).toHaveValue("0.18");
    await expect(rerank).toHaveValue("0.27");
    await expect(vector).toHaveAccessibleDescription(/全文検索で独立して一致した候補は保持/);
    await expect(rerank).toHaveAccessibleDescription(/原質問と有効な追加質問の最高関連度/);
    await vector.fill("");
    await expect(vector).toHaveAttribute("aria-invalid", "true");
    await expect(save).toBeDisabled();
    await vector.fill("1.01");
    await expect(save).toBeDisabled();
    expect(saved).toHaveLength(0);
    await vector.fill("0.3");
    await rerank.fill("0.4");
    await save.click();
    await expect(save).toBeDisabled();
    expect(saved[0]).toEqual(expect.objectContaining({ min_similarity: 0.3, rerank_min_relevance_score: 0.4 }));
    await page.reload();
    await expect(vector).toHaveValue("0.3");
    await expect(rerank).toHaveValue("0.4");
    await vector.fill("0.5");
    fail = true;
    await save.click();
    await expect(page.getByTestId("answering-settings-card").getByRole("alert")).toContainText("設定を保存できません。");
    await expect(vector).toHaveValue("0.5");
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("threshold-save-error.png") });
    await page.getByRole("button", { name: "変更を破棄", exact: true }).click();
    await expect(vector).toHaveValue("0.3");
    fail = false;
    const enabled = page.getByRole("switch", { name: "Rerank で検索候補を並べ替える", exact: true });
    await enabled.click();
    await expect(rerank).toBeDisabled();
    await expect(rerank).toHaveValue("0.4");
    await save.click();
    await expect(save).toBeDisabled();
    await expect(save).not.toHaveAttribute("aria-busy", "true");
    await expect(rerank).toBeDisabled();
    await expect(rerank).toHaveValue("0.4");
    expect(saved.at(-1)).toEqual(expect.objectContaining({ rerank_enabled: false, min_similarity: 0.3, rerank_min_relevance_score: 0.4 }));
    await vector.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("threshold-vector-settings.png") });
    await rerank.scrollIntoViewIfNeeded();
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath("threshold-rerank-settings.png") });
  });
}

test("回答の検索と生成の設定は群ごとに枠と見出しがあり、Rerank の下限は切り替えと同じ塊に入る (#4238)", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockRetrievalCards(page);
  await mockAnsweringSettings(page, []);
  await page.goto("/settings/retrieval");
  const card = page.getByTestId("answering-settings-card");
  for (const title of ["回答前の確認", "検索文の作成", "検索の実行", "検索結果の統合（RRF）", "回答の生成"]) {
    const group = card.getByRole("region", { name: title, exact: true });
    await expect(group).toBeVisible();
    await expect(group).toHaveCSS("border-top-style", "solid");
    await expect(group).toHaveCSS("border-top-width", "1px");
  }
  // 区切りの線は切り替えの行の上にだけあり、下限の欄は切り替えの行の続きとして余白を持つ。
  const rerankBlock = card.getByTestId("answering-rerank-block");
  const options = card.getByRole("group", { name: "回答の検索のオプション", exact: true });
  const boxes = await Promise.all([rerankBlock.boundingBox(), options.boundingBox()]);
  expect(Math.abs((boxes[0]?.width ?? 0) - (boxes[1]?.width ?? 0))).toBeLessThanOrEqual(2); // 親の枠の分だけ狭い
  await expect(rerankBlock.locator("#answering-rerank-min-score")).toBeVisible();
  const label = page.getByText("再ランキングの関連度スコア下限", { exact: true });
  const [labelBox, rowBox] = await Promise.all([label.boundingBox(), rerankBlock.boundingBox()]);
  expect((labelBox?.y ?? 0) - (rowBox?.y ?? 0)).toBeGreaterThan(40);
  await expectNoPageOverflow(page);
  await rerankBlock.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("rerank-block.png") });
  await card.getByRole("region", { name: "検索文の作成", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("groups.png") });
});

for (const theme of ["light", "dark"] as const) {
  test(`検索候補数と重排後 Top-N を独立して検証・保存する (${theme})`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.addInitScript((theme) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: { theme, sidebarCollapsed: true }, version: 0 })), theme);
    await mockRetrievalCards(page);
    const saved: Record<string, unknown>[] = [];
    await mockAnsweringSettings(page, saved);
    await page.goto("/settings/retrieval");
    const card = page.getByTestId("answering-settings-card");
    const search = card.getByRole("spinbutton", { name: "検索候補数の上限", exact: true });
    const topN = card.getByRole("spinbutton", { name: "再ランキング後の採用件数（Top-N）", exact: true });
    const min = card.getByRole("spinbutton", { name: "コサイン類似度の下限", exact: true });
    const score = card.getByRole("spinbutton", { name: "再ランキングの関連度スコア下限", exact: true });
    const save = card.getByRole("button", { name: "回答の設定を保存", exact: true });
    const reset = card.getByRole("button", { name: "変更を破棄", exact: true });
    await expect(search).toHaveValue("60");
    await expect(topN).toHaveValue("60");
    await expect(min).toHaveValue("0.2");
    await expect(score).toHaveValue("0.3");
    await expect(card.getByRole("switch", { name: "登録ルールによる確認の使用", exact: true })).not.toBeChecked();
    await expect(topN).toHaveAccessibleDescription(/0 は追加制限なし.*明示添付.*別枠/);
    const boxes = await Promise.all([search.boundingBox(), min.boundingBox(), topN.boundingBox(), score.boundingBox()]);
    if ((page.viewportSize()?.width ?? 0) >= 1280) {
      expect(Math.abs(boxes[0]!.y - boxes[1]!.y)).toBeLessThanOrEqual(2);
      expect(boxes[0]!.x).toBeGreaterThan(boxes[1]!.x);
      expect(Math.abs(boxes[2]!.y - boxes[3]!.y)).toBeLessThanOrEqual(2);
      expect(boxes[2]!.x).toBeGreaterThan(boxes[3]!.x);
    } else {
      expect(boxes[0]!.y).toBeGreaterThan(boxes[1]!.y);
      expect(boxes[2]!.y).toBeGreaterThan(boxes[3]!.y);
    }
    const group = card.getByRole("region", { name: "検索の実行", exact: true });
    await group.scrollIntoViewIfNeeded();
    await group.screenshot({ path: testInfo.outputPath("retrieval-count.png") });
    const rerankGroup = card.getByTestId("answering-rerank-block");
    await rerankGroup.scrollIntoViewIfNeeded();
    await rerankGroup.screenshot({ path: testInfo.outputPath("rerank-count.png") });
    for (const invalid of ["", "-1", "1.5", "2001"]) {
      await search.fill(invalid);
      await expect(save).toBeDisabled();
    }
    await search.fill("0");
    await expect(save).toBeEnabled();
    await save.click();
    expect(saved.at(-1)).toMatchObject({retrieval_query_limit: 0});
    await expect(search).toHaveAccessibleDescription(/0 は追加制限なし/);
    await search.fill("150");
    for (const invalid of ["", "-1", "1.5", "2001"]) {
      await topN.fill(invalid);
      await expect(save).toBeDisabled();
    }
    await topN.fill("2");
    await save.click();
    await expect(save).toBeDisabled();
    expect(saved.at(-1)).toMatchObject({ retrieval_query_limit: 150, rerank_top_n: 2 });
    await expect(search).toHaveValue("150");
    await expect(topN).toHaveValue("2");
    await page.reload();
    await expect(search).toHaveValue("150");
    await expect(topN).toHaveValue("2");
    await page.route("**/api/settings/answering", async route => {
      if (route.request().method() !== "PATCH") return route.fallback();
      await route.fulfill({ status: 500, json: { data: null, error_messages: ["合成の保存失敗"], warning_messages: [] } });
    });
    await search.fill("180");
    await topN.fill("3");
    await save.click();
    await expect(card).toContainText("合成の保存失敗");
    await expect(search).toHaveValue("180");
    await expect(topN).toHaveValue("3");
    await reset.click();
    await expect(search).toHaveValue("150");
    await expect(topN).toHaveValue("2");
    const rerank = card.getByRole("switch", { name: "Rerank で検索候補を並べ替える", exact: true });
    await rerank.click();
    await expect(topN).toBeDisabled();
    await expect(search).toBeEnabled();
    await reset.click();
    await expect(topN).toBeEnabled();
    await expectNoPageOverflow(page);

  });
}
