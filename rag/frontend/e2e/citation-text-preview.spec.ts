import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

// #2512: 抜粋の長い根拠で「抜粋と頁・章節」を開いても、原文のプレビューを画面の外へ押し出さない（Agent と同じ共通の
// `EvidenceSourceSheet`）。抜粋は要約の中の高さに上限のある領域でスクロールし（Tab で届き、下端のフェードで続きを
// 示す）、テキストの原本は引用した箇所へ移って強調する。内容はすべて合成のデータ。

const PROFILE = {
  id: "profile-a",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-10-18T00:00:00Z",
  updated_at: "2026-10-18T00:00:00Z",
  archived_at: null,
};

const LINES = Array.from({ length: 80 }, (_, index) => `合成の項目 ${index + 1} の本文です。`);
const DOCUMENT_TEXT = [
  "{",
  '  "items": [',
  ...LINES.map((line, index) => `    { "id": ${index + 1}, "text": "${line}" },`),
  "  ]",
  "}",
].join("\n");
const EXCERPT = LINES.slice(49, 79).join("\n");

const CITATIONS = [
  {
    document_id: "doc-a",
    chunk_id: "doc-a:cs-1:1",
    text: EXCERPT,
    score: 0.5,
    rerank_score: 0.7,
    file_name: "文書 A.json",
    category_name: null,
    metadata: { context_role: "evidence", vector_rank: 1, content_kind: "text", section_path: "第 1 章" },
  },
];

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-text",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: { retrieval_strategy: "hybrid", retrieval_strategy_adapter: "grounded" },
    })}\n\n`,
    `event: citations\ndata: ${JSON.stringify(CITATIONS)}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-text" })}\n\n`,
  ].join("");
}

async function search(page: Page) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({
      json: {
        data: { items: [PROFILE], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body: searchStreamBody() })
  );
  await page.route("**/api/documents/doc-a/content**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json; charset=utf-8", body: DOCUMENT_TEXT })
  );
  await page.route("**/api/documents/doc-a/reader?**", route => route.fulfill({ json: { data: readerWindow(EXCERPT, new URL(route.request().url()).searchParams.has("cursor")) } }));
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByTestId("evidence-card")).toHaveCount(1);
}

test("抜粋の長い根拠を開いてもプレビューは画面に残り、テキストの原本は引用の箇所を強調する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await search(page);
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.json の根拠を表示" }).click();
  const sheet = page.getByRole("dialog", { name: "検索で見つかった箇所" });
  const summary = sheet.getByTestId("evidence-source-summary");
  const toggle = summary.getByTestId("evidence-excerpt-toggle");
  await toggle.click();
  const scroll = summary.getByRole("region", { name: "抜粋と頁・章節" });
  await expect(scroll.getByTestId("citation-text")).toContainText(LINES[49]);
  // 抜粋は上限の高さの中でスクロールし、続きがあることを下端のフェードで示す。
  await expect(scroll).toHaveAttribute("data-overflowing", "true");
  await expect(scroll.getByTestId("evidence-excerpt-fade")).toHaveCSS("opacity", "1");
  const viewport = page.viewportSize()!;
  expect((await scroll.boundingBox())!.height).toBeLessThanOrEqual(viewport.height * 0.3);
  // プレビューは押し出されず、画面の中に十分な高さで残る。
  const preview = sheet.getByTestId("evidence-source-preview");
  const previewBox = (await preview.boundingBox())!;
  expect(previewBox.y + previewBox.height / 2).toBeLessThanOrEqual(viewport.height);
  expect(viewport.height - previewBox.y).toBeGreaterThanOrEqual(viewport.height * 0.35);
  const reader = preview.getByTestId("evidence-reader");
  await expect(reader.locator("mark")).toContainText(LINES[49]);
  await expect(reader).toContainText("追加の参考文脈");
  await reader.getByRole("button", { name: "後文を読む", exact: true }).click();
  await expect(reader).toContainText("合成の続き");
  await reader.getByRole("button", { name: "引用位置に戻る", exact: true }).click();
  await expect(reader.locator("mark")).toContainText(LINES[49]);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`rag-long-excerpt-${testInfo.project.name}.png`) });
  // 抜粋の領域は Tab で届き、キーボードで最後まで読める（読み終えるとフェードを消す）。
  await toggle.focus();
  await page.keyboard.press("Tab");
  await expect(scroll).toBeFocused();
  await page.keyboard.press("End");
  await expect.poll(() => scroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(scroll.getByTestId("evidence-excerpt-fade")).toHaveCSS("opacity", "0");
});

function readerWindow(quote: string, continuation: boolean) {
  return { revision: "v1", anchor_status: "exact", section_path: ["章 A", "節 A"], scope: "paragraph",
    blocks: [{ id: continuation ? "following" : "anchor", text: continuation ? "合成の続き" : quote, offset: 0, highlights: continuation ? [] : [[0, quote.length]], cited: !continuation }, { id: "context", text: "合成の前後文", offset: 0, highlights: [], cited: false }],
    previous_cursor: "before", next_cursor: continuation ? null : "after", outline: [{ id: "section-a", title: "節 A", parent_id: null, has_children: false }] };
}
