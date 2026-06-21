import { expect, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

const auth = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function adapterConfig() {
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

const kb = {
  id: "kb-1",
  name: "社内規程",
  description: "規程集",
  status: "ACTIVE" as const,
  default_search_mode: "hybrid" as const,
  document_count: 1,
  indexed_document_count: 1,
  error_document_count: 0,
  searchable_chunk_count: 12,
  created_at: "2026-06-15T00:00:00Z",
  updated_at: "2026-06-15T00:00:00Z",
  archived_at: null,
};

const doc = {
  id: "d1",
  file_name: "就業規則.pdf",
  status: "INDEXED",
  category_name: null,
  content_type: "application/pdf",
  file_size_bytes: 1024,
  content_sha256: "a".repeat(64),
  duplicate_of_document_id: null,
  uploaded_at: "2026-06-15T00:00:00Z",
  indexed_at: "2026-06-15T00:01:00Z",
  knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
};

test("KB 詳細で文書行を展開すると variant(chunk_set)が表示される", async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: auth }));
  await page.route("**/api/knowledge-bases**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: { items: [kb], total: 1, limit: 20, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: { ...kb, retrieval_config: {}, adapter_config: adapterConfig() },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/documents**", async (route) => {
    await route.fulfill({
      json: {
        data: { items: [doc], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  // 文書 d1 は 2 抽出(docling / marker)を持ち、docling 抽出は 2 chunk_set(chunking 違い)。
  await page.route("**/api/documents/d1/chunk-sets", async (route) => {
    await route.fulfill({
      json: {
        data: [
          {
            chunk_set_id: "cs_aaaaaaaaaa11",
            status: "INDEXED",
            chunk_count: 8,
            vector_count: 8,
            extraction_id: "ex_docling0001",
            parser: "docling",
            preprocess: "passthrough",
            knowledge_base_ids: ["kb-1"],
            serving_knowledge_base_ids: ["kb-1"],
          },
          {
            chunk_set_id: "cs_bbbbbbbbbb22",
            status: "INDEXED",
            chunk_count: 3,
            vector_count: 3,
            extraction_id: "ex_docling0001",
            parser: "docling",
            preprocess: "passthrough",
            knowledge_base_ids: ["kb-2"],
            serving_knowledge_base_ids: ["kb-2"],
          },
          {
            chunk_set_id: "cs_cccccccccc33",
            status: "INDEXED",
            chunk_count: 5,
            vector_count: 5,
            extraction_id: "ex_marker00001",
            parser: "marker",
            preprocess: "passthrough",
            knowledge_base_ids: ["kb-3"],
            serving_knowledge_base_ids: ["kb-3"],
          },
        ],
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/knowledge-bases/kb-1");

  const docRow = page.locator("li").filter({ hasText: "就業規則.pdf" });
  await expect(docRow).toBeVisible();
  // 展開前は variant は出ていない。
  await expect(page.getByText("8 チャンク")).toHaveCount(0);

  await docRow.getByRole("button", { name: "variant を表示" }).click();

  // 上位: 抽出(parser×前処理)が 2 グループ表示される。
  await expect(page.getByText("Parser: docling")).toBeVisible();
  await expect(page.getByText("Parser: marker")).toBeVisible();
  // docling 抽出は 2 chunk_set(chunking 違い)、marker 抽出は 1 chunk_set。
  await expect(page.getByText("2 chunk_set")).toBeVisible();
  await expect(page.getByText("1 chunk_set")).toBeVisible();
  // 下位: 各 chunk_set のチャンク数が出る。
  await expect(page.getByText("8 チャンク")).toBeVisible();
  await expect(page.getByText("3 チャンク")).toBeVisible();
  await expect(page.getByText("5 チャンク")).toBeVisible();
  // 抽出グループ(最上位リストの直下項目)は 2 つ。
  await expect(docRow.getByRole("list", { name: /抽出/ }).locator("> li")).toHaveCount(2);

  // 能動レシピ追加の導線: 別設定の KB に追加するリンクが KB 一覧へ向く。
  const addLink = docRow.getByRole("link", { name: /別設定の KB に追加/ });
  await expect(addLink).toBeVisible();
  await expect(addLink).toHaveAttribute("href", "/knowledge-bases");
});
