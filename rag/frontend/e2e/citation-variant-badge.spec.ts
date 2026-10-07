import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

const searchAnswerProfile = {
  id: "bv-1",
  name: "経理ビュー",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:00:00Z",
  archived_at: null,
};

function searchStreamBody(chunkId: string, extraMetadata: Record<string, unknown> = {}): string {
  const citation = {
    document_id: "doc-1",
    chunk_id: chunkId,
    text: "料金表の交通費は 1000 円です。申請時は領収書を添付し、利用日、経路、目的を記載してください。承認後に精算されます。長い根拠本文でもカード内では三行に収まり、全文はプレビューから確認できます。",
    score: 0.048,
    rerank_score: 0.869,
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
      ...extraMetadata,
    },
  };
  const stage = (name: string, outcome: string, elapsed_ms: number) =>
    `event: stage\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      stage: name,
      outcome,
      elapsed_ms,
      attributes: {},
    })}\n\n`;
  return [
    stage("answer", "started", 0),
    stage("answer_step:文書検索", "started", 0),
    stage("answer_step:文書検索", "success", 42),
    stage("answer", "success", 60),
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-1",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: {
        retrieval_strategy: "hybrid",
        retrieval_strategy_adapter: "grounded",
        filter_keys: ["content_kind", "section_title", "section_path"],
        knowledge_base_count: 1,
        search_answer_profile_applied: "bv-1",
        config_fingerprint: "fp-1",
      },
    })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([citation])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-1" })}\n\n`,
  ].join("");
}

test("引用カードに variant(chunk_set)バッジが出る", async ({ page }, testInfo) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false },
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  // 検索・回答プロファイルの一覧は選んでも開いたままなので閉じてから操作する（外側を押すと一覧を閉じるだけになる）。
  await page.keyboard.press("Escape");

  await page.getByText("詳細条件", { exact: true }).click();
  const topKSelect = page.getByRole("combobox", { name: "候補取得数" });
  await expect(topKSelect).toBeVisible();
  // 内容種別と見出しの条件は #649 で外した。
  await expect(page.getByRole("combobox", { name: "内容種別" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "見出しで絞り込む" })).toHaveCount(0);
  // 検索の方式と Rerank の件数は回答エンジンが使わないため選ばせない（#595）。
  await expect(page.getByRole("combobox", { name: "Rerank 採用数" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "ハイブリッド" })).toHaveCount(0);
  await topKSelect.click();
  await page.getByRole("option", { name: "50", exact: true }).click();

  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect.poll(() => searchRequests.length).toBe(1);
  for (const removed of ["mode", "strategy", "rerank_top_n", "generation_profile"]) {
    expect(removed in searchRequests[0], removed).toBe(false);
  }
  // 既定は回答を生成しない（検索結果まで。#649）。
  expect(searchRequests[0]).toMatchObject({ top_k: 50, generate_answer: false });
  expect("filters" in searchRequests[0]).toBe(false);

  const runPanel = page.getByRole("region", { name: "検索実行" });
  await expect(runPanel).toBeVisible();
  await expect(runPanel.getByText("開始")).toBeVisible();
  // 経過時間は共有の ProcessingIndicator で出す（#375）。
  await expect(runPanel.getByTestId("search-run-progress")).toContainText("検索しています");
  const elapsed = runPanel.getByTestId("search-run-progress-timer");
  await expect(elapsed).toContainText("経過時間");
  const firstElapsed = await elapsed.textContent();
  await expect.poll(() => elapsed.textContent(), { timeout: 4_000 }).not.toBe(firstElapsed);

  await expect(page.getByRole("heading", { name: /検索結果/ })).toBeVisible();
  await expect(runPanel.getByText("文書検索", { exact: true })).toBeVisible();
  await expect(runPanel.getByText("42 ms")).toBeVisible();
  // 以前の検索の内訳（検索キーワード・検索フロー・候補の表）は出さない（#595）。
  await expect(page.locator('[aria-label="検索キーワード"]')).toHaveCount(0);
  await expect(page.getByText("検索フロー")).toHaveCount(0);
  await expect(page.getByText("診断", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("table", { name: "候補詳細" })).toHaveCount(0);
  await expect(page.getByRole("meter", { name: /取得スコア/ })).toHaveCount(0);
  const rerankMeter = page.getByRole("meter", { name: "Rerank スコア: 0.869" });
  await expect(rerankMeter).toBeVisible();
  await expect(page.getByText("Both")).toBeVisible();
  await expect(page.getByText("Vector #1")).toBeVisible();
  await expect(page.getByText("Keyword #1")).toBeVisible();
  await expect(page.getByText("Rerank #1")).toBeVisible();
  // レシピバッジ(recipe_slot_no)が引用カードに表示される。
  await expect(page.getByText("レシピ1")).toBeVisible();

  const citationText = page.getByTestId("citation-text");
  const citation = citationText.locator("xpath=ancestor::li[1]");
  const citationMain = citation.getByTestId("citation-main");
  const scorePanel = citation.getByTestId("citation-score-panel");
  const rerankFill = citation.getByTestId("citation-rerank-fill");
  await expect(citationText).toBeVisible();
  await expect(scorePanel).toBeVisible();
  await expect(scorePanel.getByText("0.048", { exact: true })).toBeVisible();
  await expect
    .poll(() => citationText.evaluate((element) => getComputedStyle(element).webkitLineClamp))
    .toBe("3");

  const citationBox = await citation.boundingBox();
  const citationMainBox = await citationMain.boundingBox();
  const citationTextBox = await citationText.boundingBox();
  const scorePanelBox = await scorePanel.boundingBox();
  const rerankMeterBox = await rerankMeter.boundingBox();
  const rerankFillBox = await rerankFill.boundingBox();
  expect(citationBox).not.toBeNull();
  expect(citationMainBox).not.toBeNull();
  expect(citationTextBox).not.toBeNull();
  expect(scorePanelBox).not.toBeNull();
  expect(rerankMeterBox).not.toBeNull();
  expect(rerankFillBox).not.toBeNull();
  expect(rerankFillBox!.width / rerankMeterBox!.width).toBeCloseTo(0.869, 1);
  if (testInfo.project.name === "mobile") {
    expect(scorePanelBox!.width).toBeGreaterThan(citationBox!.width - 32);
    expect(scorePanelBox!.y).toBeGreaterThanOrEqual(citationMainBox!.y + citationMainBox!.height);
    for (const control of [
      citation.getByRole("button", { name: "policy.txt の引用箇所を表示" }),
      citation.getByRole("button", { name: "この引用は役に立った" }),
      citation.getByRole("button", { name: "この引用は役に立たなかった" }),
    ]) {
      const controlBox = await control.boundingBox();
      expect(controlBox).not.toBeNull();
      expect(controlBox!.height).toBeGreaterThanOrEqual(44);
    }
  } else {
    expect(scorePanelBox!.width).toBeCloseTo(176, 0);
    expect(scorePanelBox!.x).toBeGreaterThan(citationMainBox!.x + citationMainBox!.width);
    expect(citationTextBox!.y).toBeLessThan(scorePanelBox!.y + scorePanelBox!.height);
  }
  await expectNoPageOverflow(page);
  await citation.scrollIntoViewIfNeeded();
  await citation.screenshot({
    path: testInfo.outputPath(`citation-card-${testInfo.project.name}.png`),
  });
  await page.screenshot({
    path: testInfo.outputPath(`citation-page-${testInfo.project.name}.png`),
  });
});

test("旧版の文書の根拠には、引用カードに旧版の印を出す（#1248）", async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [searchAnswerProfile], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody("doc-1:cs_recipe1:1", {
        document_superseded: true,
        superseded_by_document_id: "doc-2",
      }),
    })
  );

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("交通費の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  const citation = page.getByTestId("citation-main");
  await expect(citation.getByText("旧版", { exact: true })).toBeVisible();
  await expect(citation.getByText("policy.txt")).toBeVisible();
  await expectNoPageOverflow(page);
});
