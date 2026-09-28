import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`文書分割設定は方式とパラメータを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockChunkingSettings(page);

    await page.goto("/settings/chunking");

    await expect(page.getByRole("heading", { name: "文書分割方式" })).toBeVisible();
    await expect(
      page.getByText("backend 内処理または pipeline-chunking へ渡す方式")
    ).toBeVisible();
    await expect(page.getByText("ここで選ぶ 7 個は分割方式です。", { exact: false })).toBeVisible();
    // DocRAG 親子階層は、削除した「親子階層」があった位置(3 番目)に並ぶ(#271)。
    const radios = page.getByRole("radio");
    await expect(radios).toHaveCount(7);
    for (const [index, name] of [
      "構造認識",
      "再帰文字分割",
      "DocRAG 親子階層",
      "見出し単位",
      "ページ単位",
      "固定長",
      "固定分割符",
    ].entries()) {
      await expect(radios.nth(index)).toContainText(name);
    }
    await expect(page.getByRole("radio", { name: /親子分割|AutoMerging/ })).toHaveCount(0);
    // 各方式カードに概念図(装飾 SVG)が 1 つずつ描画される。
    await expect(page.locator('svg[viewBox="0 0 48 36"]')).toHaveCount(7);
    await expect(page.getByRole("heading", { name: "戦略別パラメータ" })).toBeVisible();
    await expect(page.getByLabel("chunk サイズ(文字)", { exact: true })).toHaveValue("800");
    await expect(page.getByLabel("overlap(文字)")).toHaveValue("120");
    await expect(
      page.getByRole("switch", { name: "文脈ヘッダを検索対象へ追加" })
    ).toBeChecked();

    await expectStrategyParams(page, /^構造認識/, [
      "chunk サイズ(文字)",
      "overlap(文字)",
      "最小 chunk 文字数",
    ]);
    await expectStrategyParams(page, /再帰文字分割/, [
      "chunk サイズ(文字)",
      "overlap(文字)",
      "最小 chunk 文字数",
    ]);
    await expectStrategyParams(page, /DocRAG 親子階層/, DOCRAG_LABELS);
    await expect(page.getByRole("switch", { name: "文脈ヘッダを検索対象へ追加" })).toHaveCount(0);
    await expect(page.getByLabel("子チャンク目標文字数", { exact: true })).toHaveValue("1000");
    await expect(page.getByLabel("表の子チャンク目標文字数", { exact: true })).toHaveValue("3000");
    await expect(page.getByLabel("親チャンク目標文字数", { exact: true })).toHaveValue("6000");
    await expect(page.getByLabel("親チャンク最大ページ数", { exact: true })).toHaveValue("3");
    await expect(page.getByLabel("親チャンク最大 child 数", { exact: true })).toHaveValue("12");
    // 「有効パラメータ」に DocRAG の 5 項目が出る(空にならない)。
    const activeParams = page.locator("dl > div").filter({ hasText: "有効パラメータ" });
    await expect(activeParams).toContainText("子チャンク目標文字数: 1,000");
    await expect(activeParams).toContainText("表の子チャンク目標文字数: 3,000");
    await expect(activeParams).toContainText("親チャンク目標文字数: 6,000");
    await expect(activeParams).toContainText("親チャンク最大ページ数: 3");
    await expect(activeParams).toContainText("親チャンク最大 child 数: 12");
    await expectNoHorizontalOverflow(page);
    await expectSemanticStrategyParams(
      page,
      /見出し単位/,
      "見出し内の再分割上限(文字)"
    );
    await expectSemanticStrategyParams(
      page,
      /ページ単位/,
      "ページ内の再分割上限(文字)"
    );
    await expectStrategyParams(page, /^固定長 /, ["chunk サイズ(文字)", "overlap(文字)"]);

    await page.getByRole("radio", { name: /^構造認識/ }).click();
    await expect(page.getByLabel("chunk サイズ(文字)", { exact: true })).toHaveValue("800");
    await expect(page.getByLabel("overlap(文字)")).toHaveValue("120");

    await page.getByRole("radio", { name: /固定分割符/ }).click();
    await expect(page.getByRole("heading", { name: "戦略別パラメータ" })).toBeVisible();
    await expect(page.getByLabel("固定分割符文字列")).toHaveValue("\\n\\n");
    await expect(page.getByLabel("chunk サイズ(文字)", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("overlap(文字)")).toHaveCount(0);
    await expect(page.getByLabel("子チャンク目標文字数", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("最小 chunk 文字数")).toHaveCount(0);

    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    const navLink = (await openSidebarNav(page)).getByRole("link", { name: "文書分割" });
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });
}

test("文書分割設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/chunking", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["文書分割設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/chunking");

  await expect(page.getByRole("alert")).toContainText("文書分割設定を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書分割設定は DocRAG 親子階層のパラメータを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/chunking", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: chunkingEnvelope({
          strategy: "docrag_small_to_big",
          docrag_child_target_chars: 600,
          docrag_parent_max_pages: 2,
        }),
      });
      return;
    }
    await route.fulfill({ json: chunkingEnvelope() });
  });

  await page.goto("/settings/chunking");

  const docrag = page.getByRole("radio", { name: /DocRAG 親子階層/ });
  await docrag.click();
  await expect(docrag).toHaveAttribute("aria-checked", "true");

  const childTarget = page.getByLabel("子チャンク目標文字数", { exact: true });
  await childTarget.fill("2000");
  await expect(page.getByText("子チャンク目標文字数: 300〜1,600").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "保存" })).toBeDisabled();

  await childTarget.fill("600");
  await page.getByLabel("親チャンク最大ページ数", { exact: true }).fill("2");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
  await expect(page.locator("dl > div").filter({ hasText: "有効パラメータ" })).toContainText(
    "子チャンク目標文字数: 600"
  );

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("文書分割設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    strategy: "docrag_small_to_big",
    chunk_size: 800,
    overlap: 120,
    min_chars: 120,
    delimiter: "\\n\\n",
    context_header_enabled: true,
    docrag_child_target_chars: 600,
    docrag_table_child_target_chars: 3000,
    docrag_parent_target_chars: 6000,
    docrag_parent_max_pages: 2,
    docrag_parent_max_children: 12,
  });
  await expect(page.getByLabel("子チャンク目標文字数", { exact: true })).toHaveValue("600");
  await expectNoHorizontalOverflow(page);
});

test("文書分割設定は方式とパラメータを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/chunking", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: chunkingEnvelope({
          strategy: "recursive_character",
          chunk_size: 1000,
          overlap: 120,
          min_chars: 40,
          delimiter: "\\n\\n",
          context_header_enabled: false,
        }),
      });
      return;
    }
    await route.fulfill({ json: chunkingEnvelope() });
  });

  await page.goto("/settings/chunking");

  const recursive = page.getByRole("radio", { name: /再帰文字分割/ });
  await recursive.click();
  await expect(recursive).toHaveAttribute("aria-checked", "true");

  const minChars = page.getByLabel("最小 chunk 文字数");
  await minChars.fill("40");
  const chunkSize = page.getByLabel("chunk サイズ(文字)", { exact: true });
  await chunkSize.fill("1000");
  await page.getByRole("switch", { name: "文脈ヘッダを検索対象へ追加" }).click();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("文書分割設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    strategy: "recursive_character",
    chunk_size: 1000,
    overlap: 120,
    min_chars: 40,
    delimiter: "\\n\\n",
    context_header_enabled: false,
    ...DOCRAG_DEFAULTS,
  });
  await expectNoHorizontalOverflow(page);
});

test("文書分割設定は固定分割符を保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/chunking", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: chunkingEnvelope({
          strategy: "fixed_delimiter",
          delimiter: "---SECTION---",
        }),
      });
      return;
    }
    await route.fulfill({ json: chunkingEnvelope() });
  });

  await page.goto("/settings/chunking");
  await page.getByRole("radio", { name: /固定分割符/ }).click();
  await page.getByLabel("固定分割符文字列").fill("---SECTION---");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("文書分割設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    strategy: "fixed_delimiter",
    chunk_size: 800,
    overlap: 120,
    min_chars: 120,
    delimiter: "---SECTION---",
    context_header_enabled: true,
    ...DOCRAG_DEFAULTS,
  });
  await expectNoHorizontalOverflow(page);
});

const DOCRAG_LABELS = [
  "子チャンク目標文字数",
  "表の子チャンク目標文字数",
  "親チャンク目標文字数",
  "親チャンク最大ページ数",
  "親チャンク最大 child 数",
];

const DOCRAG_DEFAULTS = {
  docrag_child_target_chars: 1000,
  docrag_table_child_target_chars: 3000,
  docrag_parent_target_chars: 6000,
  docrag_parent_max_pages: 3,
  docrag_parent_max_children: 12,
};

type ChunkingOverrides = {
  strategy?: string;
  chunk_size?: number;
  overlap?: number;
  min_chars?: number;
  delimiter?: string;
  context_header_enabled?: boolean;
} & Partial<typeof DOCRAG_DEFAULTS>;

function chunkingEnvelope(overrides: ChunkingOverrides = {}) {
  const strategy = overrides.strategy ?? "structure_aware";
  const specs: { name: string; origin: string; recommended_for: string[] }[] = [
    { name: "structure_aware", origin: "ragflow_docling_marker", recommended_for: ["pdf", "office"] },
    { name: "recursive_character", origin: "langchain_recursive_character", recommended_for: ["text"] },
    {
      name: "docrag_small_to_big",
      origin: "docrag_small_to_big",
      recommended_for: ["pdf", "manual", "table", "screenshot"],
    },
    { name: "markdown_heading", origin: "markdown_header_splitter", recommended_for: ["markdown"] },
    { name: "page_level", origin: "pageindex_coarse", recommended_for: ["pdf"] },
    { name: "fixed_size", origin: "ragflow_general_fixed", recommended_for: ["text"] },
    { name: "fixed_delimiter", origin: "fixed_delimiter_split", recommended_for: ["text"] },
  ];
  return {
    data: {
      strategy,
      chunk_size: overrides.chunk_size ?? 800,
      overlap: overrides.overlap ?? 120,
      min_chars: overrides.min_chars ?? 120,
      delimiter: overrides.delimiter ?? "\\n\\n",
      context_header_enabled: overrides.context_header_enabled ?? true,
      docrag_child_target_chars:
        overrides.docrag_child_target_chars ?? DOCRAG_DEFAULTS.docrag_child_target_chars,
      docrag_table_child_target_chars:
        overrides.docrag_table_child_target_chars ??
        DOCRAG_DEFAULTS.docrag_table_child_target_chars,
      docrag_parent_target_chars:
        overrides.docrag_parent_target_chars ?? DOCRAG_DEFAULTS.docrag_parent_target_chars,
      docrag_parent_max_pages:
        overrides.docrag_parent_max_pages ?? DOCRAG_DEFAULTS.docrag_parent_max_pages,
      docrag_parent_max_children:
        overrides.docrag_parent_max_children ?? DOCRAG_DEFAULTS.docrag_parent_max_children,
      strategies: specs.map((spec) => ({
        ...spec,
        selected: spec.name === strategy,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockChunkingSettings(page: Page) {
  await page.route("**/api/settings/chunking", async (route) => {
    await route.fulfill({ json: chunkingEnvelope() });
  });
}

async function expectStrategyParams(page: Page, radioName: RegExp, visibleLabels: string[]) {
  const allLabels = [
    "chunk サイズ(文字)",
    "overlap(文字)",
    "最小 chunk 文字数",
    "固定分割符文字列",
    ...DOCRAG_LABELS,
    "見出し内の再分割上限(文字)",
    "ページ内の再分割上限(文字)",
    "再分割時の重複文字数",
  ];
  await page.getByRole("radio", { name: radioName }).click();
  for (const label of allLabels) {
    const locator = page.getByLabel(label, { exact: true });
    await expect(locator).toHaveCount(visibleLabels.includes(label) ? 1 : 0);
  }
}

async function expectSemanticStrategyParams(
  page: Page,
  radioName: RegExp,
  splitLimitLabel: string
) {
  await page.getByRole("radio", { name: radioName }).click();
  await expect(page.getByText("32,000文字超のみ再分割 / 重複なし")).toBeVisible();

  const details = page.locator("details").filter({
    hasText: "長大な単位の再分割(詳細設定)",
  });
  await expect(details).not.toHaveAttribute("open", "");
  await details.getByText("長大な単位の再分割(詳細設定)").click();
  await expect(details.getByLabel(splitLimitLabel, { exact: true })).toHaveValue("32000");
  await expect(details.getByLabel("再分割時の重複文字数")).toHaveValue("0");
  await expect(details.getByLabel("最小 chunk 文字数")).toHaveValue("120");
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
