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
// #4796: 表を求めた質問の答えは、回答エンジンが題の行の直後に表を組み立てる。
// #4812: その表は冒頭の回答に 1 回だけ出し、右端の出典の列の印（[1][2]）から表の下の番号つきの根拠の行へ移れる。
// 回答の表はセルの全文を折り返して出し（省略しない）、「すべての行を見る」を出さず、CSV は残す。
// 「資料をもとに導いた結論」には表の題と上の表の案内だけを出す。

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
const LONG_CELL = "合成の長いセルの文章です。省略せずに折り返して全文を出します。".repeat(24);
const TABLE_NOTE = "上の表を参照してください。";

const ANSWER = [
  "合成の要約です。",
  "",
  "表の題です。",
  "| 設定名 | 説明 | 出典 |",
  "| --- | --- | --- |",
  `| 設定 A | ${LONG_CELL} | [1] |`,
  "| 設定 B | 20 秒 | [1][2] |",
  "根拠：文書 A.pdf p.3",
  "根拠：文書 B.pdf p.5",
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
  TABLE_NOTE,
].join("\n");

function citation(document: string, file: string, page: number, lines: number[]) {
  return {
    document_id: document,
    chunk_id: `${document}:c-1`,
    text: CITATION_TEXT,
    score: 0.5,
    rerank_score: 0.7,
    file_name: file,
    category_name: null,
    metadata: {
      context_role: "evidence",
      content_kind: "text",
      page_start: page,
      page_end: page,
      evidence_model_used: true,
      evidence_answer_use: "confirmed",
      answer_citation_lines: lines,
      answer_citation_provenance: "child",
    },
  };
}

async function ask(page: Page, answerText = ANSWER, firstFile = "文書 A.pdf") {
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
            answer: {
              confidence: "high",
              execution_steps: [],
              evidence_tree: [],
              // 生成時の表示形式（言語資源の answer_labels）。出典の列はこの列名で見分ける。
              answer_format: { table_source_column: "出典", table_above_note: TABLE_NOTE },
            },
          },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: answerText })}\n\n`,
        `event: citations\ndata: ${JSON.stringify([
          citation("doc-a", firstFile, 3, [1, 3, 4, 5]),
          citation("doc-b", "文書 B.pdf", 5, [2]),
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
  await expect(answer).toBeVisible();
  return answer;
}

test.describe("回答の本文の表（issue 4734・4812・4822）", { tag: "@responsive" }, () => {
  test("定型の節が無い完成本文の根拠を開ける", async ({ page }, testInfo) => {
    await page.route("**/api/feedback/current?**", route => route.fulfill({ json: envelope(null) }));
    await page.route("**/api/documents/doc-a/content**", route => route.fulfill({ contentType: "text/plain; charset=utf-8", body: CITATION_TEXT }));
    await page.route("**/api/documents/doc-a/reader?**", route => route.fulfill({ json: envelope({
      revision: "synthetic-v1", anchor_status: "exact", section_path: [], scope: "paragraph",
      blocks: [{ id: "anchor", text: CITATION_TEXT, offset: 0, highlights: [[0, CITATION_TEXT.length]], cited: true }],
      previous_cursor: null, next_cursor: null, outline: [],
    }) }));
    const answer = await ask(page, "保存年数は5年です。\n根拠：synthetic-a.txt p.3", "synthetic-a.txt");
    await expect(answer).toContainText("保存年数は5年です。");
    const citation = answer.getByRole("button", { name: "[1] 根拠：synthetic-a.txt p.3 の原文を開く", exact: true });
    await expect(citation).toBeVisible();
    await citation.click();
    const source = page.getByRole("dialog", { name: "回答の根拠" });
    await expect(source).toBeVisible();
    const highlight = source.getByTestId("evidence-reader").locator("mark");
    await expect(highlight).toHaveText(CITATION_TEXT);
    await expect(highlight).toBeInViewport();
    await page.screenshot({ path: testInfo.outputPath("full-answer-citation.png") });
    await page.keyboard.press("Escape");
    await expect(citation).toBeFocused();
    await expectNoPageOverflow(page);
  });

  test("HTML 表の引用のパイプとバックスラッシュを値のまま 2 列で表示する", async ({ page }, testInfo) => {
    const answer = await ask(page, [
      "資料により確認できた事項", "", "・合成の表題",
      "| A\\|B | 10 |", "| C\\\\|D\\ | 20 |", "根拠：文書 A.pdf p.3",
    ].join("\n"));
    const table = answer.getByRole("table");
    await expect(table).toHaveCount(1);
    await expect(table.locator("thead th")).toHaveCount(2);
    await expect(table.getByRole("cell", { name: "A|B", exact: true })).toBeVisible();
    await expect(table.getByRole("cell", { name: "C\\|D\\", exact: true })).toBeVisible();
    await expect(answer).toContainText("合成の表題");
    expect(await answer.innerText()).not.toContain("<span");
    await answer.getByRole("button", { name: "すべての行を見る" }).click();
    const sheet = page.getByRole("dialog");
    await expect(sheet.getByRole("cell", { name: "A|B", exact: true })).toBeVisible();
    await expect(sheet.getByRole("cell", { name: "C\\|D\\", exact: true })).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("html-citation-cells.png") });
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();
    await expectNoPageOverflow(page);
  });

  test("冒頭の表・項目・結論の説明の表を結果の表で描き、表は冒頭に 1 回だけ出す", async ({ page }, testInfo) => {
    const answer = await ask(page);
    await page.screenshot({ path: testInfo.outputPath(`answer-table-${testInfo.project.name}.png`), fullPage: true });

    // 冒頭の表・項目の Markdown の表・コードが切り出した表の行の並び・結論の説明の表の 4 つ（結論の表は重ねない）。
    const tables = answer.getByRole("table");
    await expect(tables).toHaveCount(4);
    const text = await answer.innerText();
    expect(text).not.toContain("| ---");
    expect(text).not.toContain("| 設定名");
    expect(text).not.toContain("| 行 1 の列");
    await expect(tables.nth(0)).toContainText("設定 B");
    await expect(tables.nth(1)).toContainText("長い列の名前 8");
    await expect(tables.nth(2)).toContainText("行 2 の列");
    await expect(tables.nth(3)).toContainText("条件 A");
    // 結論の表の項目は題と上の表の案内だけ（表も根拠の行も重ねない）。
    const tableItem = answer.getByRole("listitem").filter({ hasText: "表の題です。" });
    await expect(tableItem).toContainText(TABLE_NOTE);
    await expect(tableItem.getByRole("table")).toHaveCount(0);
    await expect(tableItem.getByRole("button")).toHaveCount(0);
    await expect(answer.getByTestId("answer-application-reasoning")).toContainText("説明の文です。");

    // 回答の表もデータの結果と同じ表示（#4822）: 「すべての行を見る」と CSV を表ごとに出す。
    await expect(answer.getByRole("button", { name: "すべての行を見る" })).toHaveCount(4);
    await expect(answer.getByRole("button", { name: "CSV をダウンロード" })).toHaveCount(4);

    // 長いセルは先頭のプレビューでは 1 行で省略し（全文は title）、全文は「すべての行を見る」のシートで読む。
    const longCell = tables.nth(0).getByRole("cell", { name: LONG_CELL });
    await expect(longCell).toBeVisible();
    const preview = await longCell.locator("span").first().evaluate((element) => ({
      overflow: element.scrollWidth - element.clientWidth,
      lines: Math.round(element.getBoundingClientRect().height / Number.parseFloat(getComputedStyle(element).lineHeight)),
      title: element.getAttribute("title"),
    }));
    expect(preview.overflow).toBeGreaterThan(0);
    expect(preview.lines).toBe(1);
    expect(preview.title).toBe(LONG_CELL);
    await answer.screenshot({ path: testInfo.outputPath(`answer-table-body-${testInfo.project.name}.png`) });

    await answer.getByRole("button", { name: "すべての行を見る" }).first().click();
    const sheet = page.getByRole("dialog");
    const sheetCell = sheet.getByRole("cell", { name: LONG_CELL });
    await expect(sheetCell).toBeVisible();
    expect(
      await sheetCell.locator("span").first().evaluate((element) =>
        Math.round(element.getBoundingClientRect().height / Number.parseFloat(getComputedStyle(element).lineHeight))
      )
    ).toBeGreaterThan(1);
    const expand = sheetCell.getByRole("button", { name: "全文を表示" });
    await expect(expand).toBeVisible();
    const content = sheetCell.locator("span").first();
    expect(await content.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(1);
    await expand.click();
    await expect(sheetCell.getByRole("button", { name: "折りたたむ" })).toHaveAttribute("aria-expanded", "true");
    expect(await content.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeLessThanOrEqual(1);
    const originalViewport = page.viewportSize()!;
    const widths = originalViewport.width >= 1280 ? [1280, 1920] : [375];
    for (const width of widths) {
      await page.setViewportSize({ width, height: originalViewport.height });
      for (const dark of [false, true]) {
        await page.evaluate((value) => document.documentElement.classList.toggle("dark", value), dark);
        await page.screenshot({ path: testInfo.outputPath(`long-cell-${width}-${dark ? "dark" : "light"}.png`) });
      }
    }
    await page.setViewportSize(originalViewport);
    await page.evaluate(() => document.documentElement.classList.remove("dark"));
    await sheetCell.getByRole("button", { name: "折りたたむ" }).click();
    await expect(expand).toHaveAttribute("aria-expanded", "false");
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();

    // 表で広くなってもページははみ出さない（表の中で横にスクロールする）。
    await expectNoPageOverflow(page);
    if (testInfo.project.name === "mobile") {
      const overflow = await answer
        .getByTestId("answer-table-body-table-1-scroll")
        .nth(1)
        .evaluate((element) => element.scrollWidth - element.clientWidth);
      expect(overflow).toBeGreaterThan(0);
    }
  });

  test("出典の列の印を押すと原文を開き、すべての行のシートの中の印でも同じに開く", async ({ page }, testInfo) => {
    const answer = await ask(page);
    const table = answer.getByRole("table").first();
    await expect(table.getByRole("columnheader", { name: "出典" })).toBeVisible();
    const marks = table.getByTestId("answer-source-mark");
    await expect(marks).toHaveText(["[1]", "[1]", "[2]"]);
    // 表の下の根拠の行は番号の印の順（回答エンジンが振った番号）。
    await expect(answer.getByTestId("answer-citation-marker")).toHaveText(["[1]", "[2]"]);

    // 印を押すと、その番号の根拠の原文をそのまま開く（#4822）。閉じると印へフォーカスが戻る。
    const second = table.getByRole("link", { name: "[2] の原文を開く" });
    await second.click();
    const source = page.getByRole("dialog", { name: "回答の根拠" });
    await expect(source).toBeVisible();
    await expect(source).toContainText("文書 B.pdf");
    await page.screenshot({ path: testInfo.outputPath(`answer-table-mark-${testInfo.project.name}.png`) });
    await page.keyboard.press("Escape");
    await expect(source).toBeHidden();
    await expect(second).toBeFocused();

    // キーボードでも同じ（Enter で原文を開く）。
    await table.getByRole("link", { name: "[1] の原文を開く" }).first().focus();
    await page.keyboard.press("Enter");
    await expect(source).toBeVisible();
    await expect(source).toContainText(CITATION_TEXT);
    await page.keyboard.press("Escape");
    await expect(source).toBeHidden();

    // すべての行のシートの中の印でも原文を開き、閉じるとシートの印へ戻る（シートは開いたまま）。
    await answer.getByRole("button", { name: "すべての行を見る" }).first().click();
    const sheet = page.getByRole("dialog", { name: /回答の表/ });
    const sheetMark = sheet.getByRole("link", { name: "[2] の原文を開く" });
    await sheetMark.click();
    await expect(source).toBeVisible();
    await expect(source).toContainText("文書 B.pdf");
    await page.screenshot({ path: testInfo.outputPath(`answer-table-sheet-mark-${testInfo.project.name}.png`) });
    await page.keyboard.press("Escape");
    await expect(source).toBeHidden();
    await expect(sheet).toBeVisible();
    await expect(sheetMark).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(sheet).toBeHidden();

    // 表の下の根拠の行と、項目・結論の根拠の行は今までどおり押せる。
    await expect(answer.getByRole("button", { name: "[2] 根拠：文書 B.pdf p.5 の原文を開く" })).toBeVisible();
    await expect(answer.getByRole("button", { name: "根拠：文書 A.pdf p.3 の原文を開く", exact: true })).toHaveCount(3);
  });
});
