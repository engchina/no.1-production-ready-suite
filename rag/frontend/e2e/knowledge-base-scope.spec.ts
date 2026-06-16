import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

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
  await mockKnowledgeBases(page);
});

test("検索は選択した知識ベースをリクエストへ含める", async ({ page }) => {
  let searchPayload: Record<string, unknown> | null = null;
  await page.route("**/api/search/stream", async (route) => {
    searchPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });

  await page.goto("/search");

  await page.getByLabel(/社内規程/).check();
  await page.getByLabel("RAG 検索").fill("経費申請の承認フロー");
  await page.getByRole("button", { name: "検索" }).click();

  await expect.poll(() => searchPayload?.knowledge_base_ids).toEqual(["kb-1"]);
  await expectNoHorizontalOverflow(page);
});

test("文書インデックスは知識ベースで絞り込み、所属を表示する", async ({ page }) => {
  let lastKnowledgeBaseId: string | null = null;
  await page.route("**/api/documents**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/documents") {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    lastKnowledgeBaseId = url.searchParams.get("knowledge_base_id");
    await route.fulfill({
      json: {
        data: {
          items: documents(lastKnowledgeBaseId),
          total: documents(lastKnowledgeBaseId).length,
          limit: Number(url.searchParams.get("limit") ?? 20),
          offset: Number(url.searchParams.get("offset") ?? 0),
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/file-list");

  const filter = page.getByRole("combobox", { name: "知識ベース" });
  await filter.click();
  await page.getByRole("listbox", { name: "知識ベース" }).getByRole("option", { name: /社内規程/ }).click();

  await expect.poll(() => lastKnowledgeBaseId).toBe("kb-1");
  await expect(page.getByRole("link", { name: "policy.txt" })).toBeVisible();
  await expect(page.locator("tr").filter({ hasText: "policy.txt" }).getByText("社内規程")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("評価実行と比較実行は選択した知識ベースを使う", async ({ page }) => {
  let runPayload: Record<string, unknown> | null = null;
  let comparePayload: Record<string, unknown> | null = null;
  await page.route("**/api/evaluation/run", async (route) => {
    runPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: { data: evaluationMetrics(), error_messages: [], warning_messages: [] },
    });
  });
  await page.route("**/api/evaluation/compare", async (route) => {
    comparePayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: { data: comparisonResult(), error_messages: [], warning_messages: [] },
    });
  });

  await page.goto("/evaluation");

  await page.getByLabel(/社内規程/).check();
  await page.getByRole("button", { name: "評価実行" }).click();
  await expect.poll(() => runPayload?.knowledge_base_ids).toEqual(["kb-1"]);

  await page.getByRole("button", { name: "比較実行" }).click();
  await expect.poll(() => {
    const experiments = comparePayload?.experiments;
    return Array.isArray(experiments)
      ? experiments.map((experiment) => (experiment as Record<string, unknown>).knowledge_base_ids)
      : null;
  }).toEqual([["kb-1"], ["kb-1"]]);
  await expectNoHorizontalOverflow(page);
});

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "kb-1",
              name: "社内規程",
              description: "経費・人事・情報管理",
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 3,
              indexed_document_count: 3,
              error_document_count: 0,
              searchable_chunk_count: 16,
              created_at: "2026-06-15T00:00:00Z",
              updated_at: "2026-06-15T00:00:00Z",
              archived_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function documents(knowledgeBaseId: string | null) {
  const items = [
    {
      id: "doc-1",
      file_name: "policy.txt",
      status: "INDEXED",
      category_name: "規程",
      content_type: "text/plain",
      file_size_bytes: 120,
      content_sha256: "a".repeat(64),
      duplicate_of_document_id: null,
      uploaded_at: "2026-06-15T00:00:00Z",
      indexed_at: "2026-06-15T00:01:00Z",
      knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    },
    {
      id: "doc-2",
      file_name: "manual.txt",
      status: "UPLOADED",
      category_name: null,
      content_type: "text/plain",
      file_size_bytes: 64,
      content_sha256: "b".repeat(64),
      duplicate_of_document_id: null,
      uploaded_at: "2026-06-15T00:02:00Z",
      indexed_at: null,
      knowledge_bases: [],
    },
  ];
  return knowledgeBaseId ? items.filter((item) => item.knowledge_bases.some((kb) => kb.id === knowledgeBaseId)) : items;
}

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-kb",
      elapsed_ms: 10,
      guardrail_warnings: [],
      diagnostics: {},
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "承認フローを確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-kb" })}\n\n`,
  ].join("");
}

function evaluationMetrics() {
  return {
    case_count: 1,
    error_count: 0,
    evaluated_k: 10,
    precision_at_k: 1,
    recall_at_k: 1,
    mrr: 1,
    answer_keyword_hit_rate: 1,
    groundedness_pass_rate: 1,
    passed: true,
    threshold_failures: [],
    failure_reason_counts: {},
    ingestion_quality: {
      document_count: 1,
      table_document_count: 0,
      figure_document_count: 0,
      long_document_count: 0,
      risk_counts: { high: 0, medium: 0 },
      warning_counts: {},
      parser_profile_counts: {},
    },
    case_results: [
      {
        case_id: "policy-approval-flow-basic",
        trace_id: "trace-eval",
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
        guardrail_warnings: [],
        failure_reasons: [],
        diagnostics: {},
        elapsed_ms: 15,
        error_type: null,
        error_message: null,
      },
    ],
  };
}

function comparisonResult() {
  return {
    ranking_metric: "mrr",
    best_experiment_id: "hybrid-k10",
    results: [
      {
        rank: 1,
        ranking_score: 1,
        experiment: { id: "hybrid-k10", top_k: 10, rerank_top_n: 5, mode: "hybrid", filters: {} },
        metrics: evaluationMetrics(),
      },
      {
        rank: 2,
        ranking_score: 0.8,
        experiment: { id: "keyword-k10", top_k: 10, rerank_top_n: 5, mode: "keyword", filters: {} },
        metrics: { ...evaluationMetrics(), mrr: 0.8 },
      },
    ],
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
}
