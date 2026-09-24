import { expect, type Page, type Route, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

// rag_poc(DocRAG)移植: 業務ビューの知識(ドメインキーワード / Approved FAQ / 用語・ルール)、
// 検索前の類似問提示、DocRAG 回答の根拠パネル。

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const summary = {
  id: "bv-1",
  name: "受注サポート",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
  archived_at: null,
};

const detail = {
  ...summary,
  config: {
    version: 1,
    knowledge_base_ids: ["kb-1"],
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
      evaluation_suite: null,
      answer_engine: "docrag",
    },
    system_prompt: null,
    default_language: null,
    serving_mode: "single",
  },
  knowledge_bases: [{ id: "kb-1", name: "受注マニュアル" }],
};

const faqSuggestion = {
  id: "faq-1",
  question: "受注を取り消すには？",
  matched_question: "受注を取り消すには？",
  answer: "受注一覧で取消ボタンを押します。",
  score: 0.97,
  direct: true,
};

async function mockCommon(page: Page) {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: envelope({ mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null }),
    })
  );
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: envelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }),
    })
  );
}

async function mockBusinessViewApi(
  page: Page,
  { suggestions = [] as unknown[] }: { suggestions?: unknown[] } = {}
) {
  await page.route("**/api/business-views**", async (route: Route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/domain-keywords/suggest")) {
      return route.fulfill({
        json: envelope({
          candidates: [
            { keyword: "伝票区分", score: 1.2, frequency: 9, chunk_count: 5, document_count: 2 },
          ],
          processed_chunk_count: 20,
        }),
      });
    }
    if (path.endsWith("/domain-keywords")) {
      return route.fulfill({ json: envelope({ business_view_id: "bv-1", keywords: ["受注番号"] }) });
    }
    if (path.endsWith("/approved-faq/suggest")) {
      return route.fulfill({ json: envelope({ suggestions }) });
    }
    if (path.endsWith("/approved-faq")) {
      return route.fulfill({
        json: envelope({
          business_view_id: "bv-1",
          records: [
            {
              id: "faq-1",
              question: faqSuggestion.question,
              answer: faqSuggestion.answer,
              alternate_questions: [],
              status: "approved",
            },
          ],
        }),
      });
    }
    if (path.endsWith("/runtime-knowledge")) {
      return route.fulfill({
        json: envelope({
          business_view_id: "bv-1",
          terms: [{ term: "受注", aliases: ["オーダー"], description: "注文", status: "approved" }],
          rules: [],
        }),
      });
    }
    if (path.endsWith("/bv-1")) {
      return route.fulfill({ json: envelope(detail) });
    }
    return route.fulfill({
      json: envelope({ items: [summary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`業務ビューの知識パネルでキーワード・FAQ・用語ルールを管理できる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockCommon(page);
    await mockBusinessViewApi(page);

    await page.goto("/business-views");
    await page.getByRole("button", { name: "編集" }).first().click();

    const panel = page.getByRole("heading", { name: "業務ビューの知識" });
    await expect(panel).toBeVisible();
    await expect(page.getByLabel("登録キーワード（1 行に 1 語）")).toHaveValue("受注番号");
    await page.getByRole("button", { name: "候補を生成" }).click();
    await page.getByRole("button", { name: "伝票区分" }).click();
    await expect(page.getByLabel("登録キーワード（1 行に 1 語）")).toHaveValue("受注番号\n伝票区分");

    await page.getByRole("tab", { name: "Approved FAQ（類似問）" }).click();
    await expect(page.getByRole("rowheader", { name: faqSuggestion.question })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Excel 取込" })).toBeVisible();

    await page.getByRole("tab", { name: "用語・ルール" }).click();
    await expect(page.getByRole("rowheader", { name: "受注" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "照合テスト" })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

async function selectBusinessViewAndAsk(page: Page, question: string) {
  await page.goto("/search");
  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  await page
    .getByRole("listbox", { name: /対象の業務ビュー/ })
    .getByRole("option", { name: /受注サポート/ })
    .click();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill(question);
  await page.getByRole("button", { name: "検索", exact: true }).click();
}

test("類似する承認済み FAQ を提示し、FAQ の回答を LLM なしで表示する", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page, { suggestions: [faqSuggestion] });
  let streamed = false;
  await page.route("**/api/search/stream", (route) => {
    streamed = true;
    return route.fulfill({ status: 500 });
  });

  await selectBusinessViewAndAsk(page, "受注を取り消すには？");

  await expect(page.getByRole("heading", { name: "類似する承認済み FAQ があります" })).toBeVisible();
  await page.getByRole("button", { name: "この FAQ の回答を使う" }).click();
  await expect(page.getByRole("heading", { name: "承認済み FAQ の回答" })).toBeVisible();
  await expect(page.getByText(faqSuggestion.answer)).toBeVisible();
  expect(streamed).toBe(false);
});

test("類似問を使わない場合は DocRAG 回答と根拠パネルを表示する", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page, { suggestions: [faqSuggestion] });
  const docrag = {
    confidence: "high",
    needs_human_review: true,
    insufficient_reason: "",
    generated_queries: ["受注 取消 手順"],
    execution_steps: [{ name: "質問の理解", status: "complete", elapsed_seconds: 0.3, llm_calls: 1 }],
    evidence_tree: [
      {
        parent_id: "doc-1:p1",
        source: "manual.pdf",
        page: 3,
        children: [{ chunk_id: "doc-1:c1", role: "retrieved_anchor", is_model_used: true, page: 3 }],
      },
    ],
  };
  await page.route("**/api/search/stream", (route) =>
    route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: [
        `event: metadata\ndata: ${JSON.stringify({
          trace_id: "trace-docrag",
          elapsed_ms: 20,
          guardrail_warnings: [],
          diagnostics: { business_view_applied: "bv-1", retrieval_strategy: "docrag", docrag },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: "受注一覧で取消を押します。" })}\n\n`,
        `event: citations\ndata: ${JSON.stringify([])}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-docrag" })}\n\n`,
      ].join(""),
    })
  );

  await selectBusinessViewAndAsk(page, "受注を取り消すには？");
  await page.getByRole("button", { name: "類似問を使用しない（通常の回答生成）" }).click();

  const panel = page.getByRole("region", { name: "回答の根拠と実行記録（DocRAG）" });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("信頼度: high")).toBeVisible();
  await expect(panel.getByText("人手確認が必要")).toBeVisible();
  await panel.getByText("根拠の構成（1）").click();
  await expect(panel.getByText("検索の起点")).toBeVisible();
  await expect(panel.getByText("回答に使用")).toBeVisible();
  await expectNoPageOverflow(page);
});
