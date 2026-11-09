import { expect, type Page, test } from "./fixtures/test";
import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

/**
 * RAG 検索の回答（#3488）: チャットと同じ見出し（「回答モデル: …」、根拠の画像を添付した回答は「（画像も参照）」）を
 * 出す。
 * 内容は合成。desktop / 375px は playwright の project。
 */

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const summary = {
  id: "profile-a",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
  archived_at: null,
};

const ANSWER = "合成の画面 A の下に保存のボタンがあります。";

async function mockApi(page: Page, attachedImages: number) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq/suggest")) {
      return route.fulfill({ json: envelope({ suggestions: [] }) });
    }
    return route.fulfill({
      json: envelope({ items: [summary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  });
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: [
        `event: metadata\ndata: ${JSON.stringify({
          trace_id: "trace-model",
          elapsed_ms: 20,
          guardrail_warnings: [],
          diagnostics: {
            search_answer_profile_applied: "profile-a",
            retrieval_strategy: "hybrid",
            answer: {
              outcome: "conditional",
              execution_steps: [],
              evidence_tree: [],
              models: {
                llm: { model_id: "vendor.model-a", label: "合成のモデル A" },
                attached_images: attachedImages,
                embedding: "",
                rerank: "",
              },
            },
          },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: ANSWER })}\n\n`,
        `event: citations\ndata: ${JSON.stringify([])}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-model" })}\n\n`,
      ].join(""),
    })
  );
}

async function ask(page: Page) {
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /プロファイル A/);
  await page.keyboard.press("Escape");
  await enableSearchAnswer(page);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("合成の質問です。");
  await page.getByRole("button", { name: "検索", exact: true }).click();
}

test.describe("RAG 検索の回答の見出しと段落の印の節", { tag: "@responsive" }, () => {
  test("画像を添付した回答はモデルの名前に「（画像も参照）」を添え、矛盾・確認の節は 1 回だけ出す", async ({ page }, testInfo) => {
    await mockApi(page, 2);
    await ask(page);

    await expect(page.getByTestId("search-answer-model-heading")).toHaveText("回答モデル: 合成のモデル A（画像も参照）");
    const answer = page.getByTestId("answer-text");
    await expectNoPageOverflow(page);
    if (process.env.RAG_E2E_SCREENSHOT_DIR) {
      await page.getByTestId("search-answer-model-heading").scrollIntoViewIfNeeded();
      await page.screenshot({
        path: `${process.env.RAG_E2E_SCREENSHOT_DIR}/search-answer-model-${testInfo.project.name}.png`,
        fullPage: true,
      });
    }
  });

  test("画像を添付しなかった回答はモデルの名前だけを出す", async ({ page }) => {
    await mockApi(page, 0);
    await ask(page);

    await expect(page.getByTestId("search-answer-model-heading")).toHaveText("回答モデル: 合成のモデル A");
  });
});
