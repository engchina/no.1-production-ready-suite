import { expect, type Locator, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
] as const;

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
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of VIEWPORTS) {
test(`知識ベース詳細の所属文書は多くても高さ固定でスクロールする (${viewport.name})`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const DOC_COUNT = 30;
  const kb = {
    id: "kb-1",
    name: "社内規程",
    description: "多数の文書を含む知識ベース",
    status: "ACTIVE" as const,
    default_search_mode: "hybrid" as const,
    document_count: DOC_COUNT,
    indexed_document_count: DOC_COUNT,
    error_document_count: 0,
    searchable_chunk_count: DOC_COUNT * 4,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    archived_at: null,
  };
  await page.route("**/api/knowledge-bases**", async (route) => {
    const url = new URL(route.request().url());
    const parts = url.pathname.split("/").filter(Boolean);
    if (route.request().method() === "GET" && url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: { items: [kb], total: 1, limit: 20, offset: 0, has_next: false },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    // 所属文書は詳細ページに表示されるため、詳細 GET を返す。
    if (route.request().method() === "GET" && parts.length === 3 && parts[2] === "kb-1") {
      await route.fulfill({
        json: {
          data: { ...kb, retrieval_config: {}, adapter_config: emptyAdapterConfig() },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "not found" } });
  });
  await page.route("**/api/documents**", async (route) => {
    const items = Array.from({ length: DOC_COUNT }, (_, index) => ({
      id: `doc-${String(index + 1).padStart(2, "0")}`,
      file_name: `規程文書-${String(index + 1).padStart(2, "0")}.txt`,
      status: "INDEXED",
      category_name: null,
      content_type: "text/plain",
      file_size_bytes: 128,
      content_sha256: "a".repeat(64),
      duplicate_of_document_id: null,
      uploaded_at: "2026-06-15T00:00:00Z",
      indexed_at: "2026-06-15T00:01:00Z",
      knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    }));
    await route.fulfill({
      json: {
        data: { items, total: items.length, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/knowledge-bases/kb-1");

  const firstDoc = page.getByRole("link", { name: "規程文書-01.txt" });
  await expect(firstDoc).toBeVisible();

  const list = firstDoc.locator("xpath=ancestor::ul[1]");
  await expect(await isScrollable(list)).toBe(true);
  await expectNoPageOverflow(page);
});
}

for (const viewport of VIEWPORTS) {
test(`評価のケース結果は多くても高さ固定・ヘッダー固定でスクロールする (${viewport.name})`, async ({ page }) => {
  await page.setViewportSize({ width: viewport.width, height: viewport.height });
  const CASE_COUNT = 40;
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/evaluation/run", async (route) => {
    await route.fulfill({
      json: { data: evaluationMetrics(CASE_COUNT), error_messages: [], warning_messages: [] },
    });
  });

  await page.goto("/evaluation");

  await page.getByRole("button", { name: "評価実行" }).click();

  await expect(page.getByText("数式を含む文書")).toBeVisible();
  await expect(page.getByText("低信頼度文書")).toBeVisible();
  await expect(page.getByText("Fallback 文書")).toBeVisible();
  await expect(page.getByText("失敗 segment 文書")).toBeVisible();
  await expect(page.getByText("平均 page coverage")).toBeVisible();
  await expect(page.getByText("87.5%")).toBeVisible();

  const table = page.getByRole("table").first();
  await expect(table).toBeVisible();

  // スクロール親(max-h を持つ div)が縦スクロール可能。
  const scroller = table.locator("xpath=ancestor::div[1]");
  await expect(await isScrollable(scroller)).toBe(true);

  // ヘッダーが sticky であること。
  const headerPosition = await table
    .locator("thead")
    .evaluate((el) => getComputedStyle(el).position);
  expect(headerPosition).toBe("sticky");

  await expectNoPageOverflow(page);
});
}

async function isScrollable(locator: Locator): Promise<boolean> {
  return locator.evaluate((el) => el.scrollHeight > el.clientHeight + 1);
}

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
    },
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
      evaluation_suite: null,
    },
  };
}

function evaluationMetrics(caseCount: number) {
  const caseResults = Array.from({ length: caseCount }, (_, index) => ({
    case_id: `case-${String(index + 1).padStart(2, "0")}`,
    trace_id: `trace-${index}`.padEnd(16, "0"),
    status: "success",
    retrieved_document_ids: ["doc-1"],
    relevant_document_ids: ["doc-1"],
    hit_document_ids: ["doc-1"],
    precision_at_k: 1,
    recall_at_k: 1,
    reciprocal_rank: 1,
    answer_keyword_hit: true,
    groundedness_passed: true,
    groundedness_score: 1,
    grounding_overlap_count: 2,
    grounding_answer_feature_count: 2,
    faithfulness: 1,
    context_precision: 1,
    context_recall: 1,
    response_relevancy: 1,
    noise_sensitivity: 1,
    citation_traceability_coverage: 1,
    bbox_citation_coverage: 1,
    element_lineage_coverage: 1,
    content_kind_hit_rate: 1,
    section_coverage: 1,
    guardrail_warnings: [],
    failure_reasons: [],
    diagnostics: {},
    elapsed_ms: 12,
    error_type: null,
    error_message: null,
  }));
  return {
    case_count: caseCount,
    error_count: 0,
    evaluated_k: 10,
    precision_at_k: 1,
    recall_at_k: 1,
    mrr: 1,
    answer_keyword_hit_rate: 1,
    groundedness_pass_rate: 1,
    faithfulness: 1,
    context_precision: 1,
    context_recall: 1,
    response_relevancy: 1,
    noise_sensitivity: 1,
    citation_traceability_coverage: 1,
    bbox_citation_coverage: 1,
    element_lineage_coverage: 1,
    content_kind_hit_rate: 1,
    section_coverage: 1,
    passed: true,
    threshold_failures: [],
    failure_reason_counts: {},
    ingestion_quality: {
      document_count: caseCount,
      table_document_count: 0,
      figure_document_count: 0,
      formula_document_count: 2,
      low_confidence_document_count: 3,
      fallback_document_count: 1,
      failed_segment_document_count: 1,
      segment_artifact_cache_miss_document_count: 0,
      long_document_count: 0,
      average_page_coverage: 0.875,
      risk_counts: { low: caseCount, medium: 0, high: 0 },
      warning_counts: {},
      parser_profile_counts: {},
    },
    case_results: caseResults,
  };
}
