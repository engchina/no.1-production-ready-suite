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
  test(`Chunking 設定は戦略とパラメータを表示する (${viewport.name})`, async ({ page }) => {
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

    await expect(page.getByRole("heading", { name: "Chunking 戦略" })).toBeVisible();
    await expect(page.getByRole("radio", { name: /構造認識/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /親子階層/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /ページ単位/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: "多様化パラメータ" })).toBeVisible();
    await expect(page.getByLabel("chunk サイズ(文字)", { exact: true })).toHaveValue("800");
    await expect(page.getByLabel("overlap(文字)")).toHaveValue("120");

    const navLink = page.getByRole("link", { name: "Chunking アダプター" });
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await expectNoHorizontalOverflow(page);
  });
}

test("Chunking 設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/chunking", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["Chunking 設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/chunking");

  await expect(page.getByRole("alert")).toContainText("Chunking 設定を取得できませんでした。");
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("Chunking 設定は戦略とパラメータを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/chunking", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: chunkingEnvelope({
          strategy: "hierarchical_parent_child",
          chunk_size: 1000,
          overlap: 120,
          child_size: 300,
          sentence_window_size: 3,
          min_chars: 40,
        }),
      });
      return;
    }
    await route.fulfill({ json: chunkingEnvelope() });
  });

  await page.goto("/settings/chunking");

  const hierarchical = page.getByRole("radio", { name: /親子階層/ });
  await hierarchical.click();
  await expect(hierarchical).toHaveAttribute("aria-checked", "true");

  const childSize = page.getByLabel("子 chunk サイズ(文字)");
  await childSize.fill("300");
  const minChars = page.getByLabel("最小 chunk 文字数");
  await minChars.fill("40");
  const chunkSize = page.getByLabel("chunk サイズ(文字)", { exact: true });
  await chunkSize.fill("1000");
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("Chunking 設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    strategy: "hierarchical_parent_child",
    chunk_size: 1000,
    overlap: 120,
    child_size: 300,
    sentence_window_size: 3,
    min_chars: 40,
  });
  await expectNoHorizontalOverflow(page);
});

type ChunkingOverrides = {
  strategy?: string;
  chunk_size?: number;
  overlap?: number;
  child_size?: number;
  sentence_window_size?: number;
  min_chars?: number;
};

function chunkingEnvelope(overrides: ChunkingOverrides = {}) {
  const strategy = overrides.strategy ?? "structure_aware";
  const specs: { name: string; origin: string; recommended_for: string[] }[] = [
    { name: "structure_aware", origin: "ragflow_docling_marker", recommended_for: ["pdf", "office"] },
    { name: "recursive_character", origin: "langchain_recursive_character", recommended_for: ["text"] },
    { name: "sentence_window", origin: "llamaindex_sentence_window", recommended_for: ["faq"] },
    {
      name: "hierarchical_parent_child",
      origin: "llamaindex_auto_merging",
      recommended_for: ["long_document"],
    },
    { name: "markdown_heading", origin: "markdown_header_splitter", recommended_for: ["markdown"] },
    { name: "page_level", origin: "pageindex_coarse", recommended_for: ["pdf"] },
  ];
  return {
    data: {
      strategy,
      chunk_size: overrides.chunk_size ?? 800,
      overlap: overrides.overlap ?? 120,
      child_size: overrides.child_size ?? 320,
      sentence_window_size: overrides.sentence_window_size ?? 3,
      min_chars: overrides.min_chars ?? 0,
      strategies: specs.map((spec) => ({
        ...spec,
        selected: spec.name === strategy,
        uses_child_size: spec.name === "hierarchical_parent_child",
        uses_sentence_window: spec.name === "sentence_window",
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

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
