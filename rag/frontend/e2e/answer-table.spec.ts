import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

// #4734: 回答の本文の Markdown の表と、コードが切り出した表の行の並びを、文字のまま出さずに共通の結果の表で描く。
// 広い表は表の中で横にスクロールし、ページをはみ出さない。表の後ろの根拠の行は押せるまま。内容はすべて合成のデータ。
// #4796: 表を求めた質問の答えは、回答エンジンが題の行の直後に表を組み立てる（要約と「資料をもとに導いた結論」の項目）。

const PROFILE = {
  id: "profile-a",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-10-20T00:00:00Z",
  updated_at: "2026-10-20T00:00:00Z",
  archived_at: null,
};

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const CITATION_TEXT = "合成の根拠の本文です。";
const COLUMNS = Array.from({ length: 8 }, (_, index) => `長い列の名前 ${index + 1}`);

const ANSWER = [
  "合成の要約です。",
  "| 設定名 | 既定値 | 単位 |",
  "| --- | --- | --- |",
  "| 設定 A | 10 | 秒 |",
  "| 設定 B | 20 | 秒 |",
  "",
  "資料により確認できた事項",
  "",
  "・項目の文です。",
  "",
  `| ${COLUMNS.join(" | ")} |`,
  `| ${COLUMNS.map(() => "---").join(" | ")} |`,
  `| ${COLUMNS.map((_, index) => `長い値の文章です ${index + 1}`).join(" | ")} |`,
  "根拠：文書 A.pdf p.3",
  "・| 行 1 の列 | 100 |",
  "| 行 2 の列 | 200 |",
  "根拠：文書 A.pdf p.3",
  "",
  "資料をもとに導いた結論",
  "",
  "・結論の文です。",
  "当てはめ：説明の文です。",
  "| 条件 | 結果 |",
  "| --- | --- |",
  "| 条件 A | 結果 A |",
  "根拠：文書 A.pdf p.3",
  "・表の題です。",
  "| 項目 | 扱い |",
  "| --- | --- |",
  "| 項目 A | 扱い A |",
  "| 項目 B | 扱い B |",
  "根拠：文書 A.pdf p.3",
].join("\n");

async function ask(page: Page) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq/suggest")) return route.fulfill({ json: envelope({ suggestions: [] }) });
    return route.fulfill({ json: envelope({ items: [PROFILE], total: 1, limit: 50, offset: 0, has_next: false }) });
  });
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: [
        `event: metadata\ndata: ${JSON.stringify({
          trace_id: "trace-table",
          elapsed_ms: 10,
          guardrail_warnings: [],
          diagnostics: {
            search_answer_profile_applied: PROFILE.id,
            retrieval_strategy: "hybrid",
            answer: { confidence: "high", execution_steps: [], evidence_tree: [] },
          },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: ANSWER })}\n\n`,
        `event: citations\ndata: ${JSON.stringify([
          {
            document_id: "doc-a",
            chunk_id: "doc-a:c-1",
            text: CITATION_TEXT,
            score: 0.5,
            rerank_score: 0.7,
            file_name: "文書 A.pdf",
            category_name: null,
            metadata: {
              context_role: "evidence",
              content_kind: "text",
              page_start: 3,
              page_end: 3,
              evidence_model_used: true,
              evidence_answer_use: "confirmed",
              answer_citation_lines: [1, 2, 3],
              answer_citation_provenance: "child",
            },
          },
        ])}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-table" })}\n\n`,
      ].join(""),
    })
  );
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await enableSearchAnswer(page);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  const answer = page.getByTestId("answer-text");
  await expect(answer.getByRole("heading", { name: "資料により確認できた事項" })).toBeVisible();
  return answer;
}

test.describe("回答の本文の表（issue 4734）", { tag: "@responsive" }, () => {
  test("要約・項目・結論の説明の表を結果の表で描き、表の後ろの根拠の行を押せる", async ({ page }, testInfo) => {
    const answer = await ask(page);
    await page.screenshot({ path: testInfo.outputPath(`answer-table-${testInfo.project.name}.png`), fullPage: true });

    // 要約・項目の Markdown の表・コードが切り出した表の行の並び・結論の説明の表・結論の表の 5 つ。
    const tables = answer.getByRole("table");
    await expect(tables).toHaveCount(5);
    const text = await answer.innerText();
    expect(text).not.toContain("| ---");
    expect(text).not.toContain("| 設定名");
    expect(text).not.toContain("| 行 1 の列");
    await expect(tables.nth(0)).toContainText("設定 A");
    await expect(tables.nth(1)).toContainText("長い列の名前 8");
    await expect(tables.nth(2)).toContainText("行 2 の列");
    await expect(tables.nth(3)).toContainText("条件 A");
    await expect(tables.nth(4)).toContainText("項目 B");
    expect(text).not.toContain("| 項目 A");
    // 結論の表の題の行は表の上に文として残り、表の後ろの根拠の行はその項目の根拠になる。
    const tableItem = answer.getByRole("listitem").filter({ hasText: "表の題です。" });
    await expect(tableItem.getByRole("table")).toContainText("扱い B");
    await expect(tableItem.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" })).toHaveCount(1);
    await expect(answer.getByTestId("answer-application-reasoning")).toContainText("説明の文です。");
    await answer.screenshot({ path: testInfo.outputPath(`answer-table-body-${testInfo.project.name}.png`) });

    // 表で広くなってもページははみ出さない（表の中で横にスクロールする）。
    await expectNoPageOverflow(page);
    if (testInfo.project.name === "mobile") {
      const overflow = await answer
        .getByTestId("answer-table-body-table-1-scroll")
        .nth(1)
        .evaluate((element) => element.scrollWidth - element.clientWidth);
      expect(overflow).toBeGreaterThan(0);
    }

    // 表の後ろの根拠の行は、表が入っても押せて原文を開く。
    const citations = answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" });
    await expect(citations).toHaveCount(4);
    await citations.first().click();
    const sheet = page.getByRole("dialog", { name: "回答の根拠" });
    await expect(sheet).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`answer-table-citation-${testInfo.project.name}.png`) });
  });
});
