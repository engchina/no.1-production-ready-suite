import { expect, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const auth = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

const businessView = {
  id: "bv-1",
  name: "経理ビュー",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:00:00Z",
  archived_at: null,
};

function searchStreamBody(chunkId: string): string {
  const citation = {
    document_id: "doc-1",
    chunk_id: chunkId,
    text: "料金表の交通費は 1000 円です。",
    score: 0.91,
    rerank_score: 0.96,
    file_name: "policy.txt",
    category_name: null,
    metadata: {
      page_start: 2,
      content_kind: "table",
      context_role: "evidence",
      retrieval_mode: "hybrid",
      vector_rank: 1,
      vector_score: 0.91,
      keyword_rank: 1,
      keyword_score: 0.82,
      rrf_score: 0.032,
      rerank_rank: 1,
      recipe_id: "recipe-1",
      recipe_slot_no: 1,
    },
  };
  return [
    `event: stage\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      stage: "embedding",
      outcome: "started",
      elapsed_ms: 0,
      attributes: { input_count: 1 },
    })}\n\n`,
    `event: stage\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      stage: "embedding",
      outcome: "success",
      elapsed_ms: 42,
      attributes: { output_count: 1 },
    })}\n\n`,
    `event: stage\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      stage: "retrieval",
      outcome: "success",
      elapsed_ms: 55,
      attributes: { output_count: 1 },
    })}\n\n`,
    `event: stage\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      stage: "rerank",
      outcome: "success",
      elapsed_ms: 18,
      attributes: { output_count: 1 },
    })}\n\n`,
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: {
        retrieved_count: 1,
        reranked_count: 1,
        citation_count: 1,
        keyword_terms: ["交通費", "交通", "通費"],
        retrieval_breakdown: {
          vector_count: 1,
          keyword_count: 1,
          overlap_count: 1,
          fused_count: 1,
          fusion_dropped_count: 0,
          rerank_input_count: 1,
          rerank_kept_count: 1,
          rerank_dropped_count: 0,
          evidence_count: 1,
          citation_count: 1,
          dropped_count: 0,
        },
        retrieval_candidates: [
          {
            chunk_id: chunkId,
            document_id: "doc-1",
            file_name: "policy.txt",
            sources: ["vector", "keyword"],
            vector_rank: 1,
            vector_score: 0.91,
            keyword_rank: 1,
            keyword_score: 0.82,
            rrf_score: 0.032,
            rerank_rank: 1,
            rerank_score: 0.96,
            status: "citation",
            drop_reason: null,
          },
        ],
      },
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: "確認しました。" })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([citation])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-1" })}\n\n`,
  ].join("");
}

test("引用カードに variant(chunk_set)バッジが出る", async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: auth }));
  await page.route("**/api/business-views**", (route) =>
    route.fulfill({
      json: {
        data: { items: [businessView], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  const searchRequests: Array<Record<string, unknown>> = [];
  await page.route("**/api/search/stream", async (route) => {
    searchRequests.push(route.request().postDataJSON() as Record<string, unknown>);
    await new Promise((resolve) => setTimeout(resolve, 2200));
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      // chunk_id は document:chunk_set:index 形式 → variant バッジが出る。
      body: searchStreamBody("doc-1:cs_recipe1:1"),
    });
  });

  await page.goto("/search");
  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  await page
    .getByRole("listbox", { name: /対象の業務ビュー/ })
    .getByRole("option", { name: /経理ビュー/ })
    .click();

  await page.getByText("詳細条件", { exact: true }).click();
  const topKSelect = page.getByRole("combobox", { name: "候補取得数" });
  const rerankTopNSelect = page.getByRole("combobox", { name: "Rerank 採用数" });
  const contentKindSelect = page.getByRole("combobox", { name: "内容種別" });
  await expect(topKSelect).toBeVisible();
  await expect(rerankTopNSelect).toBeVisible();
  await expect(contentKindSelect).toBeVisible();
  await expect(page.getByLabel("見出し名")).toBeHidden();
  await rerankTopNSelect.click();
  await page.getByRole("option", { name: "10", exact: true }).click();
  await topKSelect.click();
  await page.getByRole("option", { name: "5", exact: true }).click();
  await expect(rerankTopNSelect).toContainText("5");
  await topKSelect.click();
  await page.getByRole("option", { name: "50", exact: true }).click();
  await rerankTopNSelect.click();
  await page.getByRole("option", { name: "8", exact: true }).click();
  await contentKindSelect.click();
  await page.getByRole("option", { name: "表", exact: true }).click();
  await page.getByRole("button", { name: "見出しで絞り込む" }).click();
  await page.getByLabel("見出し名").fill("料金表");
  await page.getByLabel("見出しの階層").fill("経費申請");

  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect.poll(() => searchRequests.length).toBe(1);
  expect(searchRequests[0]).toMatchObject({
    top_k: 50,
    rerank_top_n: 8,
    filters: {
      content_kind: "table",
      section_title: "料金表",
      section_path: "経費申請",
    },
  });

  const runPanel = page.getByRole("region", { name: "検索実行" });
  await expect(runPanel).toBeVisible();
  await expect(runPanel.getByText("開始")).toBeVisible();
  await expect(runPanel.getByText("経過")).toBeVisible();
  const elapsed = runPanel.getByTestId("search-run-elapsed");
  const firstElapsed = await elapsed.textContent();
  await expect.poll(() => elapsed.textContent(), { timeout: 4_000 }).not.toBe(firstElapsed);

  await expect(page.getByRole("heading", { name: /引用/ })).toBeVisible();
  await expect(runPanel.getByText("埋め込み")).toBeVisible();
  await expect(runPanel.getByText("42 ms")).toBeVisible();
  const keywordPanel = page.locator('[aria-label="検索キーワード"]');
  await expect(keywordPanel.getByText("検索キーワード")).toBeVisible();
  await expect(keywordPanel.getByText("交通費", { exact: true })).toBeVisible();
  const appliedFilters = page.locator('[aria-label="適用中の詳細条件"]');
  await expect(appliedFilters.getByText("内容種別: 表")).toBeVisible();
  await expect(appliedFilters.getByText("見出し名: 料金表")).toBeVisible();
  await expect(appliedFilters.getByText("見出しの階層: 経費申請")).toBeVisible();
  await expect(page.getByText("検索フロー")).toBeVisible();
  await expect(page.getByText("ベクトル取得")).toBeVisible();
  await expect(page.getByText("キーワード取得")).toBeVisible();
  await expect(page.getByText("詳細メトリクス")).toBeHidden();
  await expect(page.getByText("候補詳細")).toBeHidden();
  await page.getByText("診断", { exact: true }).click();
  await expect(page.getByText("詳細メトリクス")).toBeVisible();
  await expect(page.getByText("候補詳細")).toBeVisible();
  await expect(page.getByRole("meter", { name: "取得スコア: 0.910" })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Rerank スコア: 0.960" })).toBeVisible();
  await expect(page.getByText("Both")).toBeVisible();
  await expect(page.getByText("Vector #1")).toBeVisible();
  await expect(page.getByText("Keyword #1")).toBeVisible();
  await expect(page.getByText("Rerank #1")).toBeVisible();
  // レシピバッジ(recipe_slot_no)が引用カードに表示される。
  await expect(page.getByText("レシピ1")).toBeVisible();
  await expectNoPageOverflow(page);
});
