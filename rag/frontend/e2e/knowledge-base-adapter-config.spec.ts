import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function emptyAdapterConfig() {
  return {
    version: 1,
    ingestion: {
      preprocess_profile: null,
      parser_adapter_backend: null,
      parser_docling_enabled: null,
      parser_marker_enabled: null,
      parser_unstructured_enabled: null,
      chunking_strategy: null,
      chunk_size: null,
      chunk_overlap: null,
      chunk_child_size: null,
      chunk_sentence_window_size: null,
      chunk_min_chars: null,
      graph_profile: null,
      field_extraction_enabled: null,
      asset_summary_enabled: null,
      navigation_summary_enabled: null,
      auto_chunk_after_extract_enabled: null,
      auto_index_after_chunk_enabled: null,
    },
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
      vector_index_profile: null,
      evaluation_suite: null,
    },
  };
}

const summary = {
  id: "kb-1",
  name: "社内規程",
  description: "就業規則",
  status: "ACTIVE" as const,
  default_search_mode: "hybrid" as const,
  document_count: 0,
  indexed_document_count: 0,
  error_document_count: 0,
  searchable_chunk_count: 0,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
};

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/documents**", (route) =>
    route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 100, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
});

test("ナレッジベース単位で文書分割方式を上書きして保存できる", async ({ page }) => {
  let patched: { adapter_config?: { ingestion?: { chunking_strategy?: string | null } } } | null =
    null;

  await page.route("**/api/knowledge-bases**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);

    if (request.method() === "GET" && url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: { items: [summary], total: 1, limit: 20, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "GET" && parts.length === 3) {
      await route.fulfill({
        json: {
          data: { ...summary, retrieval_config: {}, adapter_config: emptyAdapterConfig() },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "PATCH" && parts.length === 3) {
      patched = request.postDataJSON();
      const config = patched?.adapter_config ?? emptyAdapterConfig();
      await route.fulfill({
        json: {
          data: { ...summary, retrieval_config: {}, adapter_config: config },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "not found" } });
  });

  await page.goto("/knowledge-bases/kb-1");

  await expect(page.getByRole("heading", { name: "構築設定", exact: true })).toBeVisible();
  await expectNoPageOverflow(page);

  // 文書分割の行で「上書き」を有効化する(リボンが同名ラベルを持つため構築 region に限定)。
  const ingestSection = page.getByRole("region", { name: "ナレッジ構築設定" });
  const chunkingRow = ingestSection
    .getByText("文書分割", { exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  await expect(chunkingRow.getByText("グローバル設定に従う")).toBeVisible();
  await chunkingRow.getByRole("button", { name: "上書き" }).click();

  // 上書き既定値(見出し単位)の選択欄が現れる。
  const select = chunkingRow.getByRole("combobox", { name: "文書分割" });
  await expect(select).toContainText("見出し単位");
  await select.click();
  await page.getByRole("option", { name: /ページ単位/ }).click();

  await page.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(page.getByText("構築設定を保存しました。")).toBeVisible();
  expect(patched?.adapter_config?.ingestion?.chunking_strategy).toBe("page_level");
});

test("ナレッジベース単位で自動進行を上書きして保存できる", async ({ page }) => {
  let patched: {
    adapter_config?: {
      ingestion?: {
        auto_chunk_after_extract_enabled?: boolean | null;
        auto_index_after_chunk_enabled?: boolean | null;
      };
    };
  } | null = null;

  await page.route("**/api/knowledge-bases**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);

    if (request.method() === "GET" && url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: { items: [summary], total: 1, limit: 20, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "GET" && parts.length === 3) {
      await route.fulfill({
        json: {
          data: { ...summary, retrieval_config: {}, adapter_config: emptyAdapterConfig() },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "PATCH" && parts.length === 3) {
      patched = request.postDataJSON();
      const config = patched?.adapter_config ?? emptyAdapterConfig();
      await route.fulfill({
        json: {
          data: { ...summary, retrieval_config: {}, adapter_config: config },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "not found" } });
  });

  await page.goto("/knowledge-bases/kb-1");

  const ingestSection = page.getByRole("region", { name: "ナレッジ構築設定" });
  const autoChunkRow = ingestSection
    .getByText("抽出後に Chunk 作成へ進む", { exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");
  const autoIndexRow = ingestSection
    .getByText("Chunk 後に Embedding / 索引へ進む", { exact: true })
    .locator("xpath=ancestor::div[contains(@class,'rounded-lg')][1]");

  await autoChunkRow.getByRole("button", { name: "上書き" }).click();
  await autoIndexRow.getByRole("button", { name: "上書き" }).click();
  await page.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(page.getByText("構築設定を保存しました。")).toBeVisible();
  expect(patched?.adapter_config?.ingestion?.auto_chunk_after_extract_enabled).toBe(true);
  expect(patched?.adapter_config?.ingestion?.auto_index_after_chunk_enabled).toBe(true);
  await expectNoPageOverflow(page);
});

test("KB legacy query 設定は構築設定画面に表示されない", async ({ page }) => {
  const withOverride = emptyAdapterConfig();
  withOverride.query.generation_profile = "detailed_cited";

  await page.route("**/api/knowledge-bases**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);
    if (request.method() === "GET" && url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: { items: [summary], total: 1, limit: 20, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (request.method() === "GET" && parts.length === 3) {
      await route.fulfill({
        json: {
          data: { ...summary, retrieval_config: {}, adapter_config: withOverride },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "not found" } });
  });

  await page.goto("/knowledge-bases/kb-1");

  await expect(page.getByRole("heading", { name: "構築設定", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "検索・回答設定(legacy)" })).toHaveCount(0);
  await expect(page.getByText("詳細・出典明示")).toHaveCount(0);
});
