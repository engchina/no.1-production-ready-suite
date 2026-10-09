import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockAuthUser, mockLocalAuth, openSidebarNav } from "./_helpers";

const SCREENSHOT_DIR = process.env.RAG_E2E_SCREENSHOT_DIR;

type PipelineSettings = {
  auto_parse_after_preprocess_enabled: boolean;
  auto_chunk_after_extract_enabled: boolean;
  auto_index_after_chunk_enabled: boolean;
};

const RECIPE_DEFAULTS = {
  preprocess_profile: "passthrough",
  parser_adapter_backend: "docling",
  parser_docling_enabled: true,
  parser_unstructured_enabled: false,
  parser_mineru_enabled: false,
  parser_dots_ocr_enabled: false,
  vision_enabled: true,
  chunking_strategy: "structure_aware",
  chunk_size: 800,
  chunk_overlap: 120,
  chunk_min_chars: 120,
  chunk_context_header_enabled: true,
  graph_profile: "off",
  entity_index_enabled: false,
  entity_name_columns: [],
  entity_attribute_columns: [],
  field_extraction_enabled: false,
  navigation_summary_enabled: false,
};

/** `/api/settings/pipeline` の GET / PATCH。PATCH の payload を記録する（#528）。 */
async function mockPipelineSettings(
  page: Page,
  initial: PipelineSettings = {
    auto_parse_after_preprocess_enabled: true,
    auto_chunk_after_extract_enabled: true,
    auto_index_after_chunk_enabled: true,
  },
  options: { delayMs?: number; fail?: boolean } = {}
) {
  const patches: unknown[] = [];
  let current = { ...initial };
  await page.route("**/api/settings/pipeline", async (route) => {
    if (options.delayMs) await new Promise((resolve) => setTimeout(resolve, options.delayMs));
    if (options.fail) {
      await route.fulfill({
        status: 503,
        json: { data: null, error_messages: ["全体の既定を取得できませんでした。"], warning_messages: [] },
      });
      return;
    }
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as Partial<PipelineSettings>;
      patches.push(payload);
      current = { ...current, ...payload };
    }
    await route.fulfill({
      json: {
        data: {
          ...current,
          recipe_defaults: { ...RECIPE_DEFAULTS, ...current },
          config_source: "runtime",
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  return patches;
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
  await mockPipelineSettings(page);
});

test("検索・回答設定の概要ハブが工程をフェーズ別カードで俯瞰し各設定へ導線を出す", async ({ page }) => {
  await page.goto("/settings/pipeline");

  await expect(page.getByRole("heading", { name: "設定の概要" })).toBeVisible();

  // 2 フェーズの見出し。
  await expect(page.getByRole("region", { name: "ナレッジ構築" })).toBeVisible();
  await expect(page.getByRole("region", { name: "検索・回答" })).toBeVisible();

  // 取込フェーズの工程カードは構築側設定へ、検索フェーズは検索側設定へ遷移する。
  await expect(page.getByRole("link", { name: "文書分割 の設定を開く" })).toHaveAttribute(
    "href",
    "/settings/chunking"
  );
  await expect(page.getByRole("link", { name: "検索方法 の設定を開く" })).toHaveAttribute(
    "href",
    "/settings/retrieval"
  );
  // 関係情報の工程は取込時の構築の設定なので「関係情報の構築」の名前でナレッジ構築フェーズに並ぶ(#301)。
  await expect(
    page
      .getByRole("region", { name: "ナレッジ構築" })
      .getByRole("link", { name: "関係情報の構築 の設定を開く" })
  ).toHaveAttribute("href", "/settings/graph");

  // カードから実際に詳細設定へ遷移できる。
  await page.getByRole("link", { name: "文書分割 の設定を開く" }).click();
  await expect(page).toHaveURL(/\/settings\/chunking$/);

  await expectNoPageOverflow(page);
});

test("サイドバーの設定の概要リンクからハブへ到達できる", async ({ page }) => {
  await page.goto("/settings/pipeline");
  const sidebar = await openSidebarNav(page);
  // 現在地がハブなので「検索・回答設定」セクションは自動展開し、画面のタイトルと同じ名前のリンクが見える（#267）。
  await expect(sidebar.getByRole("link", { name: "設定の概要" })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("サイドバーの検索・回答設定の名前と順番は設定の概要の工程と同じ（#267）", async ({ page }) => {
  await page.goto("/settings/pipeline");
  await expect(page.getByRole("heading", { name: "設定の概要" })).toBeVisible();
  // 概要の工程カード（1. 〜 12.）の名前を順番どおりに読む。
  const cardNames = await page
    .getByRole("link", { name: / の設定を開く$/ })
    .evaluateAll((links) => links.map((link) => (link.getAttribute("aria-label") ?? "").replace(/ の設定を開く$/, "")));
  expect(cardNames.length).toBeGreaterThan(0);
  const pipelineHrefs = await page
    .getByRole("link", { name: / の設定を開く$/ })
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  // サイドバーの同じ工程のリンクを、サイドバー上の並び順で読む（先頭の「設定の概要」は除く）。
  const sidebar = await openSidebarNav(page);
  const sidebarNames = await sidebar.locator("a").evaluateAll(
    (links, hrefs) =>
      links
        .filter((link) => hrefs.includes(link.getAttribute("href")))
        .map((link) => (link.textContent ?? "").trim()),
    pipelineHrefs
  );
  expect(sidebarNames).toEqual(cardNames);
});

// #528: レシピ 13 項目を、選択中レシピの設定と同じ処理順で出す（実体の索引は #1388）。
const PROCESSING_ORDER = [
  "preprocess_profile",
  "auto_parse_after_preprocess_enabled",
  "parser_adapter_backend",
  "vision_enabled",
  "field_extraction_enabled",
  "navigation_summary_enabled",
  "section_rules_mode",
  "auto_chunk_after_extract_enabled",
  "chunking_strategy",
  "chunk_context_header_enabled",
  "auto_index_after_chunk_enabled",
  "entity_index_enabled",
  "graph_profile",
];

for (const scheme of ["light", "dark"] as const) {
  test(`取込の流れは 13 項目の全体の既定を処理順に出し、工程の間のスイッチで自動進行を保存する (${scheme})`, async ({
    page,
  }, testInfo) => {
    await page.addInitScript((theme) => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { theme }, version: 0 })
      );
    }, scheme);
    const patches = await mockPipelineSettings(page, {
      auto_parse_after_preprocess_enabled: true,
      auto_chunk_after_extract_enabled: true,
      auto_index_after_chunk_enabled: false,
    });
    await page.goto("/settings/pipeline");
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(scheme === "dark");

    const flow = page.getByRole("region", { name: "取込の流れと全体の既定" });
    const items = flow.getByTestId("pipeline-recipe-defaults");
    // DOM の順（上から下）が処理順。
    await expect(items.locator("[data-config-field]")).toHaveCount(PROCESSING_ORDER.length);
    expect(
      await items
        .locator("[data-config-field]")
        .evaluateAll((elements) => elements.map((element) => element.getAttribute("data-config-field")))
    ).toEqual(PROCESSING_ORDER);
    await expect(items.getByRole("heading", { level: 3 })).toHaveText([
      "1. ファイル準備",
      "2. 抽出",
      "3. Chunk 作成",
      "4. Embedding / 索引",
    ]);
    // 全体の既定の値。
    await expect(items.locator('[data-config-field="parser_adapter_backend"]')).toContainText("Docling");
    await expect(items.locator('[data-config-field="vision_enabled"]')).toContainText("有効");
    await expect(items.locator('[data-config-field="field_extraction_enabled"]')).toContainText("無効");

    // 各項目から、その全体の既定を変える画面へ移動できる。
    await expect(
      items.getByRole("link", { name: "図・画像を AI で読み取る を設定する画面を開く" })
    ).toHaveAttribute("href", "/settings/parser-adapters#post-parse-vision");
    await expect(
      items.getByRole("link", { name: "文脈ヘッダを検索対象へ追加 を設定する画面を開く" })
    ).toHaveAttribute("href", "/settings/chunking");
    await expect(items.getByRole("link", { name: / を設定する画面を開く$/ })).toHaveCount(9);
    // 実体の索引は文書ごとに選ぶ（全体の既定は無効。変える画面は持たない。#1388）。
    const entityIndex = items.locator('[data-config-field="entity_index_enabled"]');
    await expect(entityIndex).toContainText("無効");
    await expect(entityIndex.getByRole("link")).toHaveCount(0);

    // 工程の間の 3 つのスイッチ。
    const parseGate = items.getByRole("switch", { name: "ファイル準備後に抽出へ進む" });
    const chunkGate = items.getByRole("switch", { name: "抽出後に Chunk 作成へ進む" });
    const indexGate = items.getByRole("switch", { name: "Chunk 後に Embedding / 索引へ進む" });
    await expect(parseGate).toHaveAttribute("aria-checked", "true");
    await expect(indexGate).toHaveAttribute("aria-checked", "false");
    await chunkGate.focus();
    await page.keyboard.press("Space");
    await expect(chunkGate).toHaveAttribute("aria-checked", "false");
    await expect(flow.getByText("未保存の変更があります。")).toBeVisible();

    if (SCREENSHOT_DIR) {
      await page.screenshot({
        path: `${SCREENSHOT_DIR}/pipeline-flow-${testInfo.project.name}-${scheme}.png`,
        fullPage: true,
      });
    }

    await flow.getByRole("button", { name: "保存", exact: true }).click();
    // 保存の成功は Toast（操作の行に常設の成功の表示を残さない。#992）。
    await expect(page.getByText("工程の自動進行を保存しました。")).toBeVisible();
    await expect(flow.getByText("工程の自動進行を保存しました。")).toHaveCount(0);
    expect(patches).toEqual([
      {
        auto_parse_after_preprocess_enabled: true,
        auto_chunk_after_extract_enabled: false,
        auto_index_after_chunk_enabled: false,
      },
    ]);
    await expect(items.locator('[data-config-field="auto_chunk_after_extract_enabled"]')).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("取込の流れは読み込み中に形を予約し、取得に失敗したら再試行できる", async ({ page }) => {
  await mockPipelineSettings(page, undefined, { delayMs: 1500 });
  await page.goto("/settings/pipeline");
  await expect(page.getByTestId("settings-pipeline-flow-loading")).toBeVisible();
  await expect(page.getByTestId("settings-pipeline-flow-loading")).toContainText(
    "全体の既定を読み込んでいます"
  );
  await expect(page.getByRole("switch", { name: "ファイル準備後に抽出へ進む" })).toBeVisible();

  await page.unrouteAll({ behavior: "ignoreErrors" });
  await mockLocalAuth(page);
  await mockPipelineSettings(page, undefined, { fail: true });
  await page.reload();
  const flow = page.getByRole("region", { name: "取込の流れと全体の既定" });
  await expect(flow.getByText("全体の既定を取得できませんでした。")).toBeVisible();
  await expect(flow.getByRole("button", { name: "再試行" })).toBeVisible();
  // 工程のカード（ナビ）は取得に失敗しても使える。
  await expect(page.getByRole("link", { name: "文書分割 の設定を開く" })).toBeVisible();
});

test("権限のない設定画面へのリンクは出さない", async ({ page }) => {
  await mockAuthUser(page, { permissions: ["menu.settings_pipeline", "menu.settings_chunking"] });
  await page.goto("/settings/pipeline");
  const items = page.getByTestId("pipeline-recipe-defaults");
  await expect(items.locator("[data-config-field]")).toHaveCount(PROCESSING_ORDER.length);
  await expect(items.getByRole("link", { name: / を設定する画面を開く$/ })).toHaveCount(2);
  await expect(
    items.getByRole("link", { name: "文書分割 を設定する画面を開く" })
  ).toHaveAttribute("href", "/settings/chunking");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`工程の自動進行の保存に失敗したら、スイッチを残して操作の行に失敗を出す（#992, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockPipelineSettings(page);
    await page.route("**/api/settings/pipeline", async (route) => {
      if (route.request().method() !== "PATCH") {
        await route.fallback();
        return;
      }
      await route.fulfill({
        status: 500,
        json: {
          data: null,
          error_messages: ["工程の自動進行の設定を backend/.env へ保存できませんでした。"],
          warning_messages: [],
        },
      });
    });
    await page.goto("/settings/pipeline");

    const items = page.getByTestId("pipeline-recipe-defaults");
    const indexGate = items.getByRole("switch", { name: "Chunk 後に Embedding / 索引へ進む" });
    await indexGate.click();
    const actions = page.getByRole("group", { name: "工程の自動進行の操作" });
    await expect(actions).toContainText("未保存の変更があります。");
    await actions.getByRole("button", { name: "保存", exact: true }).click();

    await expect(actions).toContainText("工程の自動進行の設定を backend/.env へ保存できませんでした。");
    await expect(indexGate).toHaveAttribute("aria-checked", "false");
    await expect(actions.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
    // 取消は「変更を破棄」で、保存値へ戻す。
    await actions.getByRole("button", { name: "変更を破棄" }).click();
    await expect(indexGate).toHaveAttribute("aria-checked", "true");
    await expectNoPageOverflow(page);
  });
}
