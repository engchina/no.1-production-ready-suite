import { readFileSync } from "node:fs";
import type { Page } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

// #2710: 本文の根拠の行は、行の主張を支える箇所（同じ親・同じ頁の見出し H ではなく図の説明 F）を開き、根拠の一覧から
// 同じ箇所を開いたときと同じ要約を出す。位置を確定できない行は「位置は確定していません」と候補の数を出す。まとまり（親）
// だけを参照した箇所は「回答で引用」ではなく「まとまりで参照」に数える。内容はすべて合成のデータ。

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

const HEADING = "合成の見出し A。注意: 合成の注意書きです。";
const OPERATION = "合成の操作の説明です。";
const FIGURE = "図の説明: 左の枠に値 B を入れると、右の枠に結果 C が出ます。";

function citation(chunkId: string, text: string, metadata: Record<string, unknown>) {
  return {
    document_id: "doc-a",
    chunk_id: chunkId,
    text,
    score: 0.5,
    rerank_score: 0.7,
    file_name: "文書 A.pdf",
    category_name: null,
    metadata: { context_role: "evidence", content_kind: "text", page_start: 3, page_end: 3, ...metadata },
  };
}

// 回答の記録の並び（H が先）。1 行目は F に確定、2 行目は H と O の候補だけ（位置が決まらない）。
const RESOLVED = [
  citation("doc-a:c-h", HEADING, {
    evidence_model_used: false,
    evidence_parent_used: true,
    answer_citation_candidate_lines: [2],
  }),
  citation("doc-a:c-o", OPERATION, { evidence_model_used: false, answer_citation_candidate_lines: [2] }),
  citation("doc-a:c-f", FIGURE, {
    evidence_model_used: true,
    evidence_answer_use: "confirmed",
    answer_citation_lines: [1],
    answer_citation_provenance: "child",
  }),
];

// 以前の回答の記録（確定の印の無い番号）。行の番号は同じ頁の H に付いている。
const LEGACY = [
  citation("doc-a:c-h", HEADING, { evidence_model_used: true, answer_citation_lines: [1] }),
  citation("doc-a:c-o", OPERATION, { evidence_model_used: true }),
  citation("doc-a:c-f", FIGURE, { evidence_model_used: true }),
];

// 2 行目の候補が 1 件だけの記録（回答はその箇所を含むまとまりを参照した。#4754）。
const SINGLE_CANDIDATE = [
  citation("doc-a:c-o", OPERATION, {
    evidence_model_used: false,
    evidence_parent_used: true,
    answer_citation_candidate_lines: [2],
  }),
  citation("doc-a:c-f", FIGURE, {
    evidence_model_used: true,
    evidence_answer_use: "confirmed",
    answer_citation_lines: [1],
    answer_citation_provenance: "child",
  }),
];

const ANSWER = [
  "結果 C は右の枠に出ます。",
  "",
  "資料により確認できた事項",
  "",
  "・左の枠に値 B を入れると、右の枠に結果 C が出ます。",
  "根拠：文書 A.pdf p.3",
  "・合成の操作を行います。",
  "根拠：文書 A.pdf p.3",
].join("\n");

async function ask(
  page: Page,
  citations: unknown[],
  format?: Record<string, string>,
  { text = ANSWER, passages, expectedHeading }: { text?: string; passages?: Record<string, unknown>; expectedHeading?: string } = {}
) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq/suggest")) {
      return route.fulfill({ json: envelope({ suggestions: [] }) });
    }
    return route.fulfill({ json: envelope({ items: [PROFILE], total: 1, limit: 50, offset: 0, has_next: false }) });
  });
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: [
        `event: metadata\ndata: ${JSON.stringify({
          trace_id: "trace-provenance",
          elapsed_ms: 10,
          guardrail_warnings: [],
          diagnostics: {
            search_answer_profile_applied: PROFILE.id,
            retrieval_strategy: "hybrid",
            answer: { confidence: "high", execution_steps: [], evidence_tree: [], answer_format: format, ...passages },
          },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: format && text === ANSWER ? ANSWER.replaceAll("資料により確認できた事項", format.rules_section).replaceAll("根拠：", format.citation_prefix).replaceAll("・", format.item_bullet).replaceAll("p.3", "頁[3]") : text })}\n\n`,
        `event: citations\ndata: ${JSON.stringify(citations)}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-provenance" })}\n\n`,
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
  await expect(answer.getByRole("heading", { name: expectedHeading ?? format?.rules_section ?? "資料により確認できた事項" }).first()).toBeVisible();
  return answer;
}

/** 開いた根拠のサイドパネルの要約（番号・文書名・抜粋）と案内。 */
async function openedSheet(page: Page) {
  const sheet = page.getByRole("dialog", { name: "回答の根拠" });
  await expect(sheet).toBeVisible();
  const summary = sheet.getByTestId("evidence-source-summary");
  await summary.getByTestId("evidence-excerpt-toggle").click();
  return {
    sheet,
    excerpt: await summary.getByTestId("citation-text").innerText(),
    number: await summary.getByTestId("citation-main").locator("span").first().innerText(),
  };
}

async function closeSheet(page: Page) {
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "回答の根拠" })).toBeHidden();
}

test.describe("本文の根拠の行と根拠の一覧の対応（issue 2710）", { tag: "@responsive" }, () => {
  test("根拠の行は主張を支える図の説明を開き、根拠の一覧から開いた同じ箇所と同じ要約を出す", async ({ page }, testInfo) => {
    const answer = await ask(page, RESOLVED);
    const lines = answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" });
    await expect(lines).toHaveCount(2);

    await lines.nth(0).click();
    const fromLine = await openedSheet(page);
    // 引用の並びの先の見出し（H）ではなく、図の説明（F。3 番目）を開き、位置の案内は出さない。
    expect(fromLine.excerpt).toBe(FIGURE);
    expect(fromLine.number).toBe("3");
    await expect(fromLine.sheet.getByTestId("evidence-source-notice")).toHaveCount(0);
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`citation-line-exact-${testInfo.project.name}.png`) });
    await closeSheet(page);

    // 根拠の一覧から同じ箇所（3 番目のカード）を開くと、本文の行から開いたときと同じ要約。
    const summary = page.locator("summary").filter({ hasText: "AI に渡した 3 件" });
    await expect(summary).toHaveText(
      "AI に渡した 3 件 ／ 回答で引用 1 件 ／ まとまりで参照 1 件"
    );
    const cards = page.getByTestId("evidence-card");
    await expect(cards).toHaveCount(3);
    // 根拠の一覧は既定で閉じている（共有の AnswerEvidence）。
    if (!(await cards.first().isVisible())) await summary.click();
    await expect(cards.nth(2)).toBeVisible();
    await expect(cards.nth(0).getByTestId("citation-main")).toContainText("まとまりで参照");
    await expect(cards.nth(0).getByTestId("citation-main")).not.toContainText("回答で引用");
    await expect(cards.nth(2).getByTestId("citation-main")).toContainText("回答で引用");
    await cards.nth(2).getByRole("button", { name: "文書 A.pdf の根拠を表示" }).click();
    const fromList = await openedSheet(page);
    expect([fromList.excerpt, fromList.number]).toEqual([fromLine.excerpt, fromLine.number]);
    await expect(fromList.sheet.getByTestId("evidence-source-notice")).toHaveCount(0);
    await closeSheet(page);
  });

  test("位置を確定できない根拠の行は、候補を開いて位置が未確定であることと候補の数を出す", async ({ page }, testInfo) => {
    const answer = await ask(page, RESOLVED);
    await answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" }).nth(1).click();
    const opened = await openedSheet(page);
    const notice = opened.sheet.getByTestId("evidence-source-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("この根拠の行の位置は確定していません");
    await expect(notice).toContainText("候補 2 件");
    // 案内は要約の上。
    const [noticeBox, summaryBox] = await Promise.all([
      notice.boundingBox(),
      opened.sheet.getByTestId("evidence-source-summary").boundingBox(),
    ]);
    expect(noticeBox!.y).toBeLessThan(summaryBox!.y);
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`citation-line-unresolved-${testInfo.project.name}.png`) });
  });

  test("候補が 1 件の根拠の行は、位置は未確定のまま「決められない」とは出さない", async ({ page }, testInfo) => {
    const answer = await ask(page, SINGLE_CANDIDATE);
    await answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" }).nth(1).click();
    const opened = await openedSheet(page);
    expect(opened.excerpt).toBe(OPERATION);
    const notice = opened.sheet.getByTestId("evidence-source-notice");
    await expect(notice).toContainText("この根拠の行の位置は確定していません");
    await expect(notice).toContainText("候補はこの 1 件だけですが");
    await expect(notice).not.toContainText("1 つに決められません");
    await expect(notice).not.toContainText("候補 1 件");
    await expectNoPageOverflow(page);
    await page.screenshot({ path: testInfo.outputPath(`citation-line-single-candidate-${testInfo.project.name}.png`) });
  });

  test("以前の回答の記録の番号は記録のまま開き、同じ頁に別の箇所があれば位置が未確定と出す", async ({ page }) => {
    const answer = await ask(page, LEGACY);
    await answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く" }).nth(0).click();
    const opened = await openedSheet(page);
    // 保存した対応（H）は書き換えない。
    expect(opened.excerpt).toBe(HEADING);
    const notice = opened.sheet.getByTestId("evidence-source-notice");
    await expect(notice).toContainText("この根拠の行の位置は確定していません");
    await expect(notice).toContainText("この回答の記録は");
    await expect(notice).toContainText("文書名と頁が合う箇所 3 件");
  });
});


test("保存した表示形式の回答でも原文を開ける", { tag: "@responsive" }, async ({ page }) => {
  const answer = await ask(page, RESOLVED, { rules_section: "確認結果[例]", citation_prefix: "出典[例]：", item_bullet: "※", page_location: "頁[{page}]" });
  const lines = answer.getByRole("button", { name: "出典[例]：文書 A.pdf 頁[3] の原文を開く" });
  await expect(lines).toHaveCount(2);
  await lines.first().click();
  const { excerpt } = await openedSheet(page);
  expect(excerpt).toContain(FIGURE);
  await closeSheet(page);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: test.info().outputPath("custom-answer-format.png"), fullPage: true });
});

// issue 3394: 印の付いた説明は「資料により確認できた事項」に入れず、別の節に置く（見出しと印の意味を合わせる）。


// 引用の抽出失敗と意味の判定を混ぜず、backend の label/detail をそのまま読む。

// 区切り線の原文は存在するが、操作の裏付けとしては使わない。本文と閲覧経路は残す。


test("意味確認未実施の操作は本文と引用を保ち、該当確認済みと数えない", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const body = "合成の操作を行います。";
  const source = citation("doc-a:c-o", OPERATION, {
    evidence_model_used: true,
    evidence_answer_use: "confirmed",
    evidence_claim_support: { scope: "answer", execution: "not_run", status: "unknown", policy: "diagnostic_only" },
    context_header_enabled: true,
    vector_rank: 1,
    keyword_rank: 1,
    answer_citation_lines: [1],
    answer_citation_provenance: "child",
  });
  const answer = await ask(page, [source], undefined, { text: ["操作手順", "・" + body, "根拠：文書 A.pdf p.3"].join("\n"), expectedHeading: "操作手順" });
  await expect(answer.getByText(body, { exact: true })).toBeVisible();
  await expect(answer).not.toContainText("未確認");
  const summary = page.locator("summary").filter({ hasText: "AI に渡した 1 件" });
  await expect(summary).toContainText("回答で引用 1 件");
  await expect(summary).not.toContainText("該当確認済み");
  await expect(summary).not.toContainText("参考");
  await summary.click();
  for (const label of ["Both", "Evidence", "有効", "内容の裏付け確認は診断のみ", "内容の裏付け確認は未実施"]) {
    await expect(page.getByTestId("evidence-card").getByText(label, { exact: true })).toHaveCount(0);
  }
  await expectNoPageOverflow(page);
  await page.getByTestId("evidence-card").scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("citation-support-not-run.png") });
  await page.getByTestId("evidence-card").getByRole("button", { name: "文書 A.pdf の根拠を表示" }).click();
  const opened = await openedSheet(page);
  expect(opened.excerpt).toBe(OPERATION);
  for (const label of ["Both", "Evidence", "有効", "内容の裏付け確認は診断のみ", "内容の裏付け確認は未実施"]) {
    await expect(opened.sheet.getByText(label, { exact: true })).toHaveCount(0);
  }
  await page.screenshot({ path: testInfo.outputPath("citation-support-hidden-source.png") });
  await closeSheet(page);
  await expect(answer.getByText(body, { exact: true })).toBeVisible();
});


for (const precise of [false, true]) {
  test(`跨頁の子に結び付いた引用は${precise ? "確定した単頁" : "終頁までの範囲"}を表示する`, {tag: "@responsive"}, async ({page}, testInfo) => {
    const item = citation("doc-a:c-range", "合成の複数頁の根拠です。", {
      page_start: 3, page_end: 4, answer_citation_lines: [1], answer_citation_provenance: "child",
      ...(precise ? {answer_citation_page_ranges: [{line: 1, page_start: 3, page_end: 3}]} : {}),
    });
    const answer = await ask(page, [item], undefined, {
      text: "資料により確認できた事項\n・合成の説明です。\n根拠：文書 A.pdf p.3",
    });
    const label = `根拠：文書 A.pdf p.${precise ? "3" : "3-4"} の原文を開く`;
    await expect(answer.getByRole("button", {name: label, exact: true})).toBeVisible();
    await page.screenshot({path: testInfo.outputPath(`citation-range-${precise ? "single" : "multiple"}.png`)});
    await answer.getByRole("button", {name: label, exact: true}).click();
    expect((await openedSheet(page)).excerpt).toBe(item.text);
    await expectNoPageOverflow(page);
  });
}
