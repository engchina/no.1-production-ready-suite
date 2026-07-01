import { expect, type Page, test } from "@playwright/test";

import type { DocumentProcessingConfig, DocumentRecipeStep } from "../src/lib/api";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

const documentDetail = {
  id: "doc-1",
  file_name: "policy.pdf",
  status: "INDEXED",
  category_name: null,
  content_type: "application/pdf",
  file_size_bytes: 1024,
  content_sha256: "abc",
  duplicate_of_document_id: null,
  uploaded_at: "2026-06-15T00:00:00Z",
  indexed_at: "2026-06-15T00:05:00Z",
  knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  object_storage_path: "indexed/doc-1.pdf",
  extraction: {},
  error_message: null,
};

function ok(data: unknown) {
  return { data, error_messages: [], warning_messages: [] };
}

function config(): DocumentProcessingConfig {
  return {
    preprocess_profile: null,
    parser_adapter_backend: null,
    parser_docling_enabled: null,
    parser_marker_enabled: null,
    parser_unstructured_enabled: null,
    parser_unlimited_ocr_enabled: null,
    parser_mineru_enabled: null,
    parser_dots_ocr_enabled: null,
    parser_glm_ocr_enabled: null,
    chunking_strategy: null,
    chunk_size: 800,
    chunk_overlap: null,
    chunk_child_size: null,
    chunk_sentence_window_size: null,
    chunk_min_chars: null,
    graph_profile: null,
    field_extraction_enabled: null,
    asset_summary_enabled: null,
    navigation_summary_enabled: null,
    auto_parse_after_preprocess_enabled: null,
    auto_chunk_after_extract_enabled: null,
    auto_index_after_chunk_enabled: null,
  };
}

const effectiveBase: DocumentProcessingConfig = {
  ...config(),
  preprocess_profile: "office_to_pdf",
  parser_adapter_backend: "docling",
  chunking_strategy: "page_level",
  graph_profile: "off",
  field_extraction_enabled: false,
  asset_summary_enabled: false,
  navigation_summary_enabled: false,
  auto_parse_after_preprocess_enabled: false,
  auto_chunk_after_extract_enabled: false,
  auto_index_after_chunk_enabled: false,
};

function recipeSteps(status: string): DocumentRecipeStep[] {
  const phases = ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"] as const;
  const completedCount: Record<string, number> = {
    UPLOADED: 0,
    REVIEW: 2,
    INDEXED: 4,
  };
  return phases.map((phase, index) => ({
    phase,
    status: index < (completedCount[status] ?? 0) ? "SUCCEEDED" : "PENDING",
    started_at: null,
    finished_at: null,
    error_message: null,
  }));
}

async function mockWorkspace(
  page: Page,
  options: { documentStatus?: string; putFails?: boolean } = {}
) {
  const status = options.documentStatus ?? "INDEXED";
  let processing = config();
  let saved: DocumentProcessingConfig | null = null;
  let ingestionPosts = 0;
  const recipeResponse = () => {
    const effective = { ...effectiveBase };
    for (const [key, value] of Object.entries(processing)) {
      if (value !== null) Object.assign(effective, { [key]: value });
    }
    return {
      recipe_id: "recipe-1",
      document_id: "doc-1",
      slot_no: 1 as const,
      status,
      failed_phase: null,
      processing_config: processing,
      effective_processing_config: effective,
      preprocess_artifact: null,
      active_extraction_recipe_id: status === "INDEXED" ? "er-recipe-1-r1" : null,
      active_chunk_set_id: status === "INDEXED" ? "chunk-set-recipe-1" : null,
      chunk_count: status === "INDEXED" ? 2 : 0,
      vector_count: status === "INDEXED" ? 2 : 0,
      config_revision: 1,
      materialized_revision: status === "INDEXED" ? 1 : null,
      searchable: status === "INDEXED",
      needs_reprocessing: false,
      error_message: null,
      steps: recipeSteps(status),
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:20Z",
      started_at: null,
      finished_at: status === "INDEXED" ? "2026-06-15T00:00:20Z" : null,
    };
  };

  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({
        items: [{ id: "kb-1", name: "社内規程" }],
        total: 1,
        limit: 100,
        offset: 0,
        has_next: false,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/recipes", (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: ok([recipeResponse()]) });
    }
    return route.fulfill({ status: 404, json: { data: null, error_messages: [], warning_messages: [] } });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1", (route) => {
    if (route.request().method() === "PUT") {
      if (options.putFails) {
        return route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: ["取込ジョブの実行中は設定を変更できません。"],
            warning_messages: [],
          },
        });
      }
      saved = route.request().postDataJSON() as DocumentProcessingConfig;
      processing = saved;
    }
    return route.fulfill({ json: ok(recipeResponse()) });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/ingestion-jobs**", (route) => {
    if (route.request().method() === "POST") ingestionPosts += 1;
    route.fulfill({ json: ok([]) });
  });
  await page.route("**/api/documents/doc-1/recipes/recipe-1/chunks", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/extraction-export**", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        file_name: "policy.pdf",
        format: "markdown",
        content_type: "text/markdown",
        content: "",
        payload: {},
        chunks: [],
        parser_backend: "docling",
        parser_profile: "docling",
        page_count: 0,
        element_count: 0,
        table_count: 0,
        asset_count: 0,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/chunk-sets", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/ingestion-segments", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/knowledge-bases", (route) =>
    route.fulfill({ json: ok([{ id: "kb-1", name: "社内規程" }]) })
  );
  await page.route("**/api/documents/doc-1/content**", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/content**", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) =>
    route.fulfill({
      json: ok({ ...documentDetail, status }),
    })
  );

  return {
    saved: () => saved,
    ingestionPosts: () => ingestionPosts,
  };
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("文書処理設定を保存し、手動再処理を案内する", async ({ page }) => {
  const state = await mockWorkspace(page);
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await expect(panel).toContainText("Office→PDF");
  await expect(panel).toContainText("Docling");
  await panel.getByRole("button", { name: "処理設定を編集" }).click();

  await panel.getByRole("group", { name: "文書解析" }).getByText("上書き").click();
  await panel.getByRole("combobox", { name: "文書解析" }).click();
  await page.getByRole("option", { name: "MinerU" }).click();
  await panel.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(page.getByText(/この文書の処理設定を保存しました/)).toBeVisible();
  expect(state.saved()).toMatchObject({ parser_adapter_backend: "mineru", chunk_size: 800 });
  expect(state.ingestionPosts()).toBe(0);
  await expectNoPageOverflow(page);
});

test("保存失敗時は編集値を保持する", async ({ page }) => {
  await mockWorkspace(page, { putFails: true });
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await panel.getByRole("button", { name: "処理設定を編集" }).click();
  await panel.getByRole("group", { name: "文書解析" }).getByText("上書き").click();
  await panel.getByRole("combobox", { name: "文書解析" }).click();
  await page.getByRole("option", { name: "MinerU" }).click();
  await panel.getByRole("button", { name: "構築設定を保存" }).click();

  await expect(panel).toContainText("取込ジョブの実行中は設定を変更できません");
  await expect(panel.getByRole("combobox", { name: "文書解析" })).toContainText("MinerU");
});

test("処理途中の文書は設定を編集できない", async ({ page }) => {
  await mockWorkspace(page, { documentStatus: "REVIEW" });
  await page.goto("/documents/doc-1");

  const panel = page.getByRole("region", { name: "処理レシピ" });
  await panel.getByRole("button", { name: "処理設定を編集" }).click();

  await expect(panel).toContainText("処理途中または文書処理の実行中は設定を変更できません");
  await expect(
    panel.getByRole("group", { name: "文書解析" }).getByText("上書き")
  ).toBeDisabled();
  await expectNoPageOverflow(page);
});
