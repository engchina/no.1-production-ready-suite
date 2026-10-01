import { expect, type Page, type Route, test } from "@playwright/test";
import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectBusinessView,
} from "./_helpers";

// rag_poc からの移植: 業務ビューの知識(Approved FAQ / 用語・同義語 / ドメインキーワード / 回答ルール)、
// 検索前の類似問提示、回答の根拠パネル。

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
      guardrail_policy: null,
      answer_engine: "grounded",
    },
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
  await mockLocalAuth(page);
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
    await page.getByRole("link", { name: "受注サポート を編集" }).click();
    await expect(page).toHaveURL(/\/business-views\?id=bv-1$/);

    const panel = page.getByRole("heading", { name: "業務ビューの知識" });
    await expect(panel).toBeVisible();
    // 先頭・既定のタブは Approved FAQ（#636）。以降は回答フローで使う順（#682）。
    const tabs = page.getByRole("tablist", { name: "業務ビューの知識" }).getByRole("tab");
    await expect(tabs).toHaveText([
      "Approved FAQ（類似問）",
      "用語・同義語",
      "ドメインキーワード",
      "回答ルール",
    ]);
    await expect(page.getByRole("tab", { name: "Approved FAQ（類似問）" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(page.getByRole("rowheader", { name: faqSuggestion.question })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Excel 取込" })).toBeVisible();

    await page.getByRole("tab", { name: "ドメインキーワード" }).click();
    await expect(page.getByLabel("登録キーワード（1 行に 1 語）")).toHaveValue("受注番号");
    await page.getByRole("button", { name: "候補を生成" }).click();
    await page.getByRole("button", { name: "伝票区分" }).click();
    await expect(page.getByLabel("登録キーワード（1 行に 1 語）")).toHaveValue("受注番号\n伝票区分");

    await page.getByRole("tab", { name: "用語・同義語" }).click();
    await expect(page.getByRole("rowheader", { name: "受注" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "照合テスト" })).toBeVisible();
    // 種類の選択欄は無く、タブの種類だけを扱う（#682）。
    await expect(page.getByRole("heading", { name: "用語・同義語を追加" })).toBeVisible();
    await expect(page.locator("#runtime-knowledge-kind")).toHaveCount(0);
    // 用語・同義語は行のクリック（キーボードは名前のボタン）でフォームへ読み込み、選んだ行を aria-current で示す（#147）。
    const termRow = page.getByRole("row").filter({ has: page.getByRole("rowheader", { name: "受注" }) });
    await termRow.getByText("オーダー").click();
    await expect(termRow).toHaveAttribute("aria-current", "true");
    await expect(page.getByRole("heading", { name: "編集中: 受注" })).toBeVisible();
    await expect(page.getByLabel("同義語（1 行に 1 つ）")).toHaveValue("オーダー");
    await expect(page.getByRole("button", { name: "受注 を編集" })).toBeVisible();
    // 削除は確認ダイアログを通す（キャンセルでは送らない）。
    await page.getByRole("button", { name: "削除", exact: true }).click();
    const deleteDialog = page.getByRole("alertdialog", { name: "この用語・同義語を削除しますか？" });
    await expect(deleteDialog).toBeVisible();
    await deleteDialog.getByRole("button", { name: "キャンセル" }).click();
    await expect(deleteDialog).toHaveCount(0);
    await expectNoPageOverflow(page);
  });
}

async function selectBusinessViewAndAsk(page: Page, question: string) {
  await page.goto("/search");
  await selectBusinessView(page, /受注サポート/);
  await enableSearchAnswer(page);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill(question);
  await page.getByRole("button", { name: "検索", exact: true }).click();
}

test("よく聞かれている質問を入力欄の下に出し、選ぶと質問欄に入る", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page);
  const requested: string[] = [];
  await page.route("**/api/business-views/bv-1/query-suggestions**", (route) => {
    requested.push(new URL(route.request().url()).searchParams.get("q") ?? "");
    return route.fulfill({
      json: envelope({
        business_view_id: "bv-1",
        enabled: true,
        suggestions: [
          { question: "受注を取り消すには？", count: 5 },
          { question: "受注の締め日は？", count: 3 },
        ],
      }),
    });
  });

  await page.goto("/search");
  await selectBusinessView(page, /受注サポート/);
  const input = page.getByRole("textbox", { name: "RAG 検索" });
  await input.fill("受注");
  const suggestions = page.getByRole("list", { name: "よく聞かれている質問" });
  await expect(suggestions.getByRole("button")).toHaveCount(2);
  await expect.poll(() => requested.at(-1)).toBe("受注");

  await suggestions.getByRole("button", { name: "受注を取り消すには？" }).click();
  await expect(input).toHaveValue("受注を取り消すには？");
  // 質問欄と同じ候補は出さない。
  await expect(suggestions.getByRole("button")).toHaveCount(1);
  await expectNoPageOverflow(page);
});

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

test("類似問を使わない場合は回答と根拠パネルを表示する", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page, { suggestions: [faqSuggestion] });
  await mockAnswerStream(page);

  await selectBusinessViewAndAsk(page, "受注を取り消すには？");
  await page.getByRole("button", { name: "類似問を使用しない" }).click();

  const panel = page.getByRole("region", { name: "回答の根拠と実行記録" });
  await expect(panel).toBeVisible();
  await expect(panel.getByText("信頼度: high")).toBeVisible();
  await expect(panel.getByText("人手確認が必要")).toBeVisible();
  await panel.getByText("根拠の構成（1）").click();
  await expect(panel.getByText("検索の起点")).toBeVisible();
  await expect(panel.getByText("回答に使用")).toBeVisible();
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答を標準回答で評価し、評価の基準の指標と合否を表示する (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockCommon(page);
    await mockBusinessViewApi(page);
    await mockAnswerStream(page);
    let evaluationBody: Record<string, unknown> | null = null;
    await page.route("**/api/search/answers/trace-answer/evaluation", (route) => {
      evaluationBody = route.request().postDataJSON() as Record<string, unknown>;
      return route.fulfill({
        json: envelope({
          trace_id: "trace-answer",
          business_view_id: "bv-1",
          surface: "search",
          answer_engine: "grounded",
          question: "受注を取り消すには？",
          rewritten_question: null,
          confidence: "high",
          created_at: "2026-09-26T01:00:00Z",
          answer: "受注一覧で取消を押します。",
          citations: [],
          answer_diagnostics: {},
          evaluation_available: true,
          evaluation: {
            status: "completed",
            message: "",
            passed: false,
            suite: "standard",
            metrics: [
              { name: "faithfulness", value: 0.6, threshold: 0.7, passed: false, reference: true },
              { name: "claim_support_rate", value: 1, threshold: 0.9, passed: true },
              { name: "requirement_coverage", value: 0.5, threshold: 0.8, passed: false },
              { name: "refusal_accuracy", value: 1, threshold: 0.9, passed: true },
            ],
            standard_answer: "受注一覧で対象を選び、取消を押します。",
            evaluated_at: "2026-09-26T01:01:00Z",
            standard_answer_scope: {
              requirements: [{ requirement: "受注一覧で対象を選ぶ" }, { requirement: "取消を押す" }],
            },
            coverage_checks: [
              { requirement_index: 1, status: "missing", answer_quote: "" },
              { requirement_index: 2, status: "addressed", answer_quote: "取消を押します" },
            ],
            claim_checks: [
              { answer_quote: "受注一覧で取消を押します。", status: "supported", reason: "手順書に記載" },
            ],
            external_data_items: [],
          },
        }),
      });
    });

    await selectBusinessViewAndAsk(page, "受注を取り消すには？");

    const evaluation = page.getByRole("region", { name: "標準回答による評価" });
    const run = evaluation.getByRole("button", { name: "標準回答で評価" });
    await expect(run).toBeDisabled();
    await evaluation.getByLabel("標準回答").fill("受注一覧で対象を選び、取消を押します。");
    await run.click();

    await expect.poll(() => evaluationBody).toEqual({
      standard_answer: "受注一覧で対象を選び、取消を押します。",
    });
    // 指標ごとに値・基準・判定を出し、1 つでも閾値未満なら不合格（#680）。
    await expect(evaluation.getByText("不合格", { exact: true })).toBeVisible();
    await expect(evaluation.getByText("評価の基準: 標準")).toBeVisible();
    const coverage = evaluation.getByRole("row", { name: /標準回答の網羅/ });
    await expect(coverage).toContainText("50%");
    await expect(coverage).toContainText("80%");
    await expect(coverage).toContainText("閾値未満");
    await expect(evaluation.getByRole("row", { name: /主張の裏付け/ })).toContainText("閾値以上");
    // 根拠への忠実さ（語句の一致の近似）は参考値で、合否に使わない。
    await expect(evaluation.getByRole("row", { name: /根拠への忠実さ/ })).toContainText("参考");
    await evaluation.getByText("標準回答の項目への対応（2）").click();
    await expect(evaluation.getByText("未対応")).toBeVisible();
    await expect(evaluation.getByText("受注一覧で対象を選ぶ")).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

async function mockAnswerStream(page: Page) {
  const answerDiagnostics = {
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
          trace_id: "trace-answer",
          elapsed_ms: 20,
          guardrail_warnings: [],
          diagnostics: { business_view_applied: "bv-1", retrieval_strategy: "hybrid", answer: answerDiagnostics },
        })}\n\n`,
        `event: delta\ndata: ${JSON.stringify({ text: "受注一覧で取消を押します。" })}\n\n`,
        `event: citations\ndata: ${JSON.stringify([])}\n\n`,
        `event: done\ndata: ${JSON.stringify({ trace_id: "trace-answer" })}\n\n`,
      ].join(""),
    })
  );
}

// #540 / #541: FAQ の追加と回答ルールの保存は、押せる状態のまま未入力を欄の直下に出す。
// ルールは backend と同じく「ルール ID」「ルール名」「ルール内容」が必須。
test("FAQ の追加と回答ルールの保存は、未入力を欄の下に出す", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page);
  const writes: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && /approved-faq$|runtime-knowledge\/edit$/.test(request.url())) {
      writes.push(request.url());
    }
  });

  await page.goto("/business-views?id=bv-1");
  await page.getByRole("tab", { name: "Approved FAQ（類似問）" }).click();
  const add = page.getByRole("button", { name: "追加", exact: true });
  await expect(add).toBeEnabled();
  await add.click();
  await expect(page.locator("#approved-faq-question")).toHaveAccessibleDescription(/質問を入力してください。/);
  await expect(page.locator("#approved-faq-answer")).toHaveAccessibleDescription(/回答を入力してください。/);
  await expect(page.locator("#approved-faq-question")).toBeFocused();

  await page.getByRole("tab", { name: "回答ルール" }).click();
  await expect(page.getByRole("heading", { name: "回答ルールを追加" })).toBeVisible();
  await expect(page.locator('label[for="runtime-knowledge-title"]')).toContainText("必須");
  await expect(page.locator('label[for="runtime-knowledge-content"]')).toContainText("必須");
  // ヘッダーの「保存」（業務ビューの設定。#618）と区別し、回答ルールのタブの中の保存を押す。
  await page
    .getByRole("tabpanel", { name: "回答ルール" })
    .getByRole("button", { name: "保存", exact: true })
    .click();
  await expect(page.locator("#runtime-knowledge-name")).toHaveAccessibleDescription(/ルール ID を入力してください。/);
  await expect(page.locator("#runtime-knowledge-title")).toHaveAccessibleDescription(/ルール名を入力してください。/);
  await expect(page.locator("#runtime-knowledge-content")).toHaveAccessibleDescription(/ルール内容を入力してください。/);
  await expect(page.locator("#runtime-knowledge-name")).toBeFocused();
  expect(writes).toHaveLength(0);
  await expectNoPageOverflow(page);
});

test("業務ビューごとに類似問の提示をオン / オフできる（既定はオン。#684）", async ({ page }) => {
  await mockCommon(page);
  await mockBusinessViewApi(page);
  const saved: unknown[] = [];
  await page.route("**/api/business-views/bv-1/approved-faq/settings", async (route) => {
    const body = route.request().postDataJSON() as { enabled: boolean };
    saved.push(body);
    await route.fulfill({
      json: envelope({ business_view_id: "bv-1", records: [faqSuggestion], enabled: body.enabled }),
    });
  });

  await page.goto("/business-views?id=bv-1");
  const toggle = page.getByRole("switch", { name: "回答の前に類似問を提示する" });
  await expect(toggle).toHaveAttribute("aria-checked", "true");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-checked", "false");
  expect(saved).toEqual([{ enabled: false }]);
  await expectNoPageOverflow(page);
});
