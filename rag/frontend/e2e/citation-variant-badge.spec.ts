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
    metadata: { page_start: 2, content_kind: "table" },
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
  await page.route("**/api/search/stream", async (route) => {
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
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

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
  await expect(page.getByRole("meter", { name: "取得スコア: 0.910" })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Rerank スコア: 0.960" })).toBeVisible();
  // variant バッジ(短縮 chunk_set id)が引用カードに表示される。
  await expect(page.getByText("variant cs_recip")).toBeVisible();
  await expectNoPageOverflow(page);
});
