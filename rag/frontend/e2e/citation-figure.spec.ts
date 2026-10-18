import type { Page, Route } from "@playwright/test";

import { expect, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, selectSearchAnswerProfile } from "./_helpers";

// #2428: 図の根拠の「図を開く」（Agent と同じ共通の部品・文言・位置）。根拠のカードの操作の行と、根拠のサイドパネルの
// 要約の操作の行に出し、図でない根拠には出さない。画像は RAG の画面用の API（`GET /api/figures?document_id=&chunk_id=`）。
// 内容はすべて合成のデータ。

// 120×60 の単色の PNG（RAG が返す図の画像の代わり）。
const FIGURE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAHgAAAA8CAIAAAAiz+n/AAAAaklEQVR42u3QQQ0AAAgEoEtiMBMbyxbOBxsJSPVwIApEi0a0aNEWRItGtGjRFkSLRrRo0YgWjWjRohEtGtGiRSNaNKJFi0a0aESLFo1o0YgWLRrRohEtWjSiRSNatGhEi0a0aNGIFo3oXxYAgSoOxqX2IQAAAABJRU5ErkJggg==",
  "base64"
);

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

function citation(chunkId: string, fileName: string, metadata: Record<string, unknown>) {
  return {
    document_id: "doc-a",
    chunk_id: chunkId,
    text: `合成の本文（${chunkId}）。`,
    score: 0.5,
    rerank_score: 0.7,
    file_name: fileName,
    category_name: null,
    metadata: { context_role: "evidence", vector_rank: 1, ...metadata },
  };
}

const CITATIONS = [
  citation("doc-a:cs-1:1", "文書 A.pdf", {
    content_kind: "figure",
    figure_text_source: "vision",
    page_start: 2,
    section_path: "第 1 章",
  }),
  citation("doc-a:cs-1:2", "文書 B.pdf", { content_kind: "text", page_start: 1 }),
];

function searchStreamBody(): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-figure",
      elapsed_ms: 12,
      guardrail_warnings: [],
      diagnostics: { retrieval_strategy: "hybrid", retrieval_strategy_adapter: "grounded" },
    })}\n\n`,
    `event: citations\ndata: ${JSON.stringify(CITATIONS)}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-figure" })}\n\n`,
  ].join("");
}

/** 図の画像の API への応答。読んだ URL を返す（`statuses` の順に status を返し、尽きたら 200）。 */
async function serveFigures(page: Page, statuses: { status: number; message?: string }[] = []) {
  const seen: string[] = [];
  await page.route("**/api/figures?**", async (route: Route) => {
    seen.push(route.request().url());
    const next = statuses.shift();
    if (next && next.status !== 200) {
      await route.fulfill({
        status: next.status,
        json: { data: null, error_messages: [next.message ?? "失敗"], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "image/png",
      headers: { "Cache-Control": "private, no-store" },
      body: FIGURE_PNG,
    });
  });
  return seen;
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
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問");
  await page.getByRole("button", { name: "検索", exact: true }).click();
  await expect(page.getByTestId("evidence-card")).toHaveCount(2);
}

test("図の根拠のカードとサイドパネルに「図を開く」を出し、元の図を開く", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const figures = await serveFigures(page);
  await search(page);
  const cards = page.getByTestId("evidence-card");
  // 図の根拠だけに出し、AI が読んだ説明であることを添える（Agent と同じ文言）。
  await expect(page.getByRole("button", { name: /^図を開く/ })).toHaveCount(1);
  await expect(cards.nth(1).getByRole("button", { name: /^図を開く/ })).toHaveCount(0);
  await expect(cards.nth(0).getByTestId("evidence-figure-description")).toHaveText(
    "AI が図を読んだ説明です。元の図で確かめてください。"
  );
  const open = cards.nth(0).getByRole("button", { name: "図を開く: 文書 A.pdf" });
  // 「根拠を表示」の後ろ（同じ操作の行）。
  const sourceButton = cards.nth(0).getByRole("button", { name: "文書 A.pdf の根拠を表示" });
  const [sourceBox, openBox] = await Promise.all([sourceButton.boundingBox(), open.boundingBox()]);
  if (testInfo.project.name === "mobile") expect(openBox!.y).toBeGreaterThan(sourceBox!.y);
  else expect(openBox!.x).toBeGreaterThan(sourceBox!.x);
  await cards.nth(0).screenshot({ path: testInfo.outputPath(`rag-figure-card-${testInfo.project.name}.png`) });
  await open.click();

  const sheet = page.getByRole("dialog", { name: "図の根拠: 文書 A.pdf" });
  const image = sheet.getByRole("img", { name: "文書 A.pdf の図（p.2 · 第 1 章）" });
  await expect(image).toBeVisible();
  await expect(sheet.getByTestId("evidence-figure-loading")).toHaveCount(0);
  expect(figures).toHaveLength(1);
  const url = new URL(figures[0]);
  expect(url.searchParams.get("document_id")).toBe("doc-a");
  expect(url.searchParams.get("chunk_id")).toBe("doc-a:cs-1:1");
  // 右端に付き、375px は全画面のシート。画像はパネルに収まる。
  await expect
    .poll(async () => {
      const box = await sheet.boundingBox();
      return box ? Math.round(box.x + box.width) : -1;
    })
    .toBe(page.viewportSize()!.width);
  expect((await image.boundingBox())!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`rag-figure-${testInfo.project.name}.png`) });
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();
  await expect(open).toBeFocused();

  // 根拠のサイドパネルの要約の操作の行にも同じ「図を開く」を出す。図でない根拠には出さない。
  await sourceButton.click();
  const sourceSheet = page.getByRole("dialog", { name: "検索で見つかった箇所" });
  const summary = sourceSheet.getByTestId("evidence-source-summary");
  await expect(summary.getByRole("button", { name: "図を開く: 文書 A.pdf" })).toBeVisible();
  await summary.getByRole("button", { name: "図を開く: 文書 A.pdf" }).click();
  await expect(page.getByRole("dialog", { name: "図の根拠: 文書 A.pdf" }).getByRole("img")).toBeVisible();
  // 開き直すと画像を取り直す。
  expect(figures).toHaveLength(2);
});

test("図を取れないときは理由を出し、もう一度読み込める", async ({ page }) => {
  const figures = await serveFigures(page, [
    { status: 409, message: "この図は文書の古い版のものです。もう一度検索してください。" },
  ]);
  await search(page);
  await page.getByRole("button", { name: "図を開く: 文書 A.pdf" }).click();
  const sheet = page.getByRole("dialog", { name: "図の根拠: 文書 A.pdf" });
  const error = sheet.getByTestId("evidence-figure-error");
  await expect(error).toContainText("図を開けませんでした");
  await expect(error).toContainText("この図は文書の古い版のものです。もう一度検索してください。");
  await error.getByRole("button", { name: "もう一度読み込む" }).click();
  await expect(sheet.getByRole("img", { name: /文書 A.pdf の図/ })).toBeVisible();
  await expect(sheet.getByTestId("evidence-figure-error")).toHaveCount(0);
  expect(figures).toHaveLength(2);
});
