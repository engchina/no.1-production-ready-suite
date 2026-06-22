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

test("KB 詳細で文書行を展開するとチャンク構成と派生情報が表示される", async ({ page }) => {
  let reingestQueued = false;
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
            extraction_recipe_id: "er_aaaaaaaaaa00",
            extraction_status: "materialized",
            extraction_reason: "解析 artifact は構築済みです。",
            status: "INDEXED",
            chunk_count: 8,
            vector_count: 8,
            extraction_id: "ex_docling0001",
            parser: "docling",
            preprocess: "passthrough",
            knowledge_base_ids: ["kb-1"],
            serving_knowledge_base_ids: ["kb-1"],
            layer_statuses: {
              metadata: {
                layer_id: null,
                requested: false,
                status: "not_requested",
                reason: "現在の構築設定では項目抽出を使用しません。",
              },
              graph: {
                layer_id: null,
                requested: false,
                status: "not_requested",
                reason: "現在の構築設定では関係情報を使用しません。",
              },
              navigation: {
                layer_id: null,
                requested: false,
                status: "not_requested",
                reason: "現在の構築設定ではナビゲーションを使用しません。",
              },
            },
          },
          {
            chunk_set_id: "cs_bbbbbbbbbb22",
            extraction_recipe_id: "er_aaaaaaaaaa00",
            extraction_status: "materialized",
            extraction_reason: "解析 artifact は構築済みです。",
            status: "INDEXED",
            chunk_count: 3,
            vector_count: 3,
            extraction_id: "ex_docling0001",
            parser: "docling",
            preprocess: "passthrough",
            knowledge_base_ids: ["kb-2"],
            serving_knowledge_base_ids: ["kb-2"],
            layer_statuses: {
              metadata: {
                layer_id: "md_1111111111111111",
                requested: true,
                status: "needs_reingest",
                reason: "項目抽出には現在の構築設定で再取込が必要です。",
              },
              graph: {
                layer_id: "gr_2222222222222222",
                requested: true,
                status: "planned_only",
                reason: "関係情報は構築計画に含まれていますが、まだ実体化していません。",
              },
              navigation: {
                layer_id: "nv_3333333333333333",
                requested: true,
                status: "planned_only",
                reason: "ナビゲーションは構築計画に含まれていますが、まだ実体化していません。",
              },
            },
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
  await page.route("**/api/documents/d1/ingestion-jobs**", async (route) => {
    reingestQueued = true;
    await route.fulfill({
      json: {
        data: {
          id: "job-reingest-1",
          document_id: "d1",
          status: "QUEUED",
          phase: "EXTRACT",
          parser_profile: "default",
          quality_warnings: [],
          skip_reason: null,
          error_message: null,
          attempt_count: 0,
          max_attempts: 3,
          queued_at: "2026-06-15T00:05:00Z",
          started_at: null,
          finished_at: null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/knowledge-bases/kb-1");

  const docRow = page.locator("li").filter({ hasText: "就業規則.pdf" });
  await expect(docRow).toBeVisible();
  // 展開前はチャンク構成は出ていない。
  await expect(page.getByText("8 チャンク")).toHaveCount(0);

  await docRow.getByRole("button", { name: "チャンク構成を表示" }).click();

  // 3 つのチャンク構成が状態・チャンク数・配信 KB 数・派生情報つきで表示される。
  await expect(page.getByText("8 チャンク")).toBeVisible();
  await expect(page.getByText("3 チャンク")).toBeVisible();
  await expect(page.getByText("5 チャンク")).toBeVisible();
  const chunkSetList = docRow.getByRole("list", { name: "チャンク構成" });
  await expect(chunkSetList.getByText("解析状態", { exact: true }).first()).toBeVisible();
  await expect(chunkSetList.getByText("構築済み", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("配信 1 KB").first()).toBeVisible();
  await expect(chunkSetList.getByText("項目抽出", { exact: true }).first()).toBeVisible();
  await expect(chunkSetList.getByText("計画のみ", { exact: true }).first()).toBeVisible();
  await expect(chunkSetList.getByRole("listitem")).toHaveCount(3);

  const reingestNotice = page.getByText("現在の構築設定で再取込が必要です", {
    exact: true,
  });
  await expect(reingestNotice).toBeVisible();
  await expect(page.getByText("項目抽出には現在の構築設定で再取込が必要です。")).toBeVisible();
  await page.getByRole("button", { name: "現在の設定で再取込" }).click();
  await expect(page.getByText("再取込を開始しました。")).toBeVisible();
  expect(reingestQueued).toBe(true);
});
