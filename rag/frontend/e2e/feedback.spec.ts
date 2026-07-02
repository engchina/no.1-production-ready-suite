import { expect, test, type Page, type Route } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
  await page.route("**/api/business-views**", (route) =>
    route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "bv-1",
              name: "経理ビュー",
              description: null,
              status: "ACTIVE",
              knowledge_base_count: 1,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
              archived_at: null,
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );
});

test("高密度一覧を検索・数値ページングし、三つの詳細タブから原因を追える", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop table contract");
  const requestedUrls: string[] = [];
  await mockFeedback(page, requestedUrls);

  await page.goto("/feedback?period=30&sort=newest&size=50&page=1");

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "問題の概要" })).toBeVisible();
  await expect(page.getByRole("table").getByText("最新の経費申請期限を教えて", { exact: true })).toBeVisible();
  await expect(page.getByText("前期間比 +17pt", { exact: true }).first()).toBeVisible();

  await page.getByRole("searchbox", { name: "問題・回答・コメントを検索" }).fill("申請期限");
  await expect.poll(() => requestedUrls.some((url) => url.includes("q=%E7%94%B3%E8%AB%8B%E6%9C%9F%E9%99%90"))).toBe(true);

  await page.getByRole("button", { name: "2ページへ移動" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect.poll(() => requestedUrls.some((url) => url.includes("offset=50"))).toBe(true);

  await page.getByRole("combobox", { name: "1ページの表示件数" }).click();
  await page.getByRole("option", { name: "25件" }).click();
  await expect(page).toHaveURL(/size=25/);
  await expect(page).toHaveURL(/page=1/);

  const detailButton = page.getByRole("button", { name: "詳細を開く" }).first();
  await detailButton.click();
  await expect(page).toHaveURL(/feedback=feedback-answer/);
  await expect(page.getByRole("dialog", { name: "フィードバック詳細" })).toBeVisible();
  await expect(page.getByText("2024年版の経費規程では翌月10日です。", { exact: true })).toBeVisible();
  await expect(page.getByText("規程が更新されています。", { exact: true })).toBeVisible();

  await page.getByRole("tab", { name: "根拠" }).click();
  await expect(page.getByRole("tabpanel", { name: "根拠" }).getByText("評価対象", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "引用元を開く" })).toHaveAttribute(
    "href",
    "/documents/doc-1?chunk_id=doc-1%3A0"
  );

  await page.getByRole("tab", { name: "実行情報" }).click();
  await expect(
    page.getByRole("tabpanel", { name: "実行情報" }).getByText("oci.generativeai.command-r-plus", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "会話を開く" })).toHaveAttribute(
    "href",
    "/chat?business_view_id=bv-1&conversation_id=conv-1#message-msg-1"
  );

  await page.screenshot({ path: testInfo.outputPath("feedback-root-cause-desktop.png"), fullPage: true });

  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "フィードバック詳細" })).toBeHidden();
  await expect(detailButton).toBeFocused();
  await expect(page).not.toHaveURL(/feedback=/);
  await expectNoPageOverflow(page);
});

test("375pxではカード一覧と全画面詳細になり、URLから状態を復元できる", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile card contract");
  await page.setViewportSize({ width: 375, height: 812 });
  await mockFeedback(page, []);

  await page.goto(
    "/feedback?period=7&target=answer&rating=not_helpful&reason=incorrect&sort=newest&size=25&page=1&feedback=feedback-answer"
  );

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "問題の概要" })).toBeHidden();
  await expect(page.getByRole("dialog", { name: "フィードバック詳細" })).toBeVisible();
  const dialogBox = await page.getByRole("dialog", { name: "フィードバック詳細" }).boundingBox();
  expect(dialogBox?.width).toBe(375);

  await page.getByRole("tab", { name: "根拠" }).click();
  await expect(page.getByText("経費規程.pdf", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "詳細を閉じる" }).click();
  await expect(page.locator("li").getByText("最新の経費申請期限を教えて", { exact: true })).toBeVisible();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("feedback-root-cause-mobile.png"), fullPage: true });
});

async function mockFeedback(page: Page, requestedUrls: string[]) {
  await page.route("**/api/feedback**", async (route) => {
    requestedUrls.push(route.request().url());
    await fulfillFeedback(route);
  });
}

async function fulfillFeedback(route: Route) {
  const url = new URL(route.request().url());
  if (url.pathname === "/api/feedback/feedback-answer") {
    await route.fulfill({ json: feedbackDetailEnvelope() });
    return;
  }
  const limit = Number(url.searchParams.get("limit") ?? 50);
  const offset = Number(url.searchParams.get("offset") ?? 0);
  await route.fulfill({ json: feedbackEnvelope(limit, offset) });
}

function feedbackEnvelope(limit: number, offset: number) {
  return {
    data: {
      summary: {
        total: 120,
        helpful_count: 80,
        not_helpful_count: 40,
        helpful_rate: 0.6667,
        answer_total: 80,
        answer_helpful_rate: 0.5,
        citation_total: 40,
        citation_helpful_rate: 1,
        reason_counts: [
          { reason: "incorrect", count: 24 },
          { reason: "incomplete", count: 10 },
          { reason: "not_relevant", count: 6 },
        ],
      },
      previous_summary: {
        total: 100,
        helpful_count: 50,
        not_helpful_count: 50,
        helpful_rate: 0.5,
        answer_total: 70,
        answer_helpful_rate: 0.4,
        citation_total: 30,
        citation_helpful_rate: 0.7333,
        reason_counts: [{ reason: "incorrect", count: 30 }],
      },
      items: {
        items: [
          {
            feedback_id: "feedback-answer",
            trace_id: "trace-answer-123456789",
            business_view_id: "bv-1",
            business_view_name: "経理ビュー",
            target_type: "answer",
            source_surface: "chat",
            document_id: null,
            chunk_id: null,
            message_id: "msg-1",
            rating: "not_helpful",
            reason: "incorrect",
            comment: "規程が更新されています。",
            created_at: "2026-07-01T09:00:00Z",
            conversation_id: "conv-1",
            conversation_title: "経費申請",
            model: "oci.generativeai.command-r-plus",
            file_name: null,
            question_preview: "最新の経費申請期限を教えて",
            comment_preview: "規程が更新されています。",
            has_comment: true,
          },
          {
            feedback_id: "feedback-citation",
            trace_id: "trace-citation-123456789",
            business_view_id: "bv-1",
            business_view_name: "経理ビュー",
            target_type: "citation",
            source_surface: "search",
            document_id: "doc-1",
            chunk_id: "doc-1:0",
            message_id: null,
            rating: "helpful",
            reason: null,
            comment: null,
            created_at: "2026-07-01T08:00:00Z",
            conversation_id: null,
            conversation_title: null,
            model: null,
            file_name: "経費規程.pdf",
            question_preview: "申請期限を確認したい",
            comment_preview: null,
            has_comment: false,
          },
          ...Array.from({ length: 8 }, (_, index) => ({
            feedback_id: `feedback-extra-${index + 1}`,
            trace_id: `trace-extra-${index + 1}`,
            business_view_id: "bv-1",
            business_view_name: "経理ビュー",
            target_type: index % 2 === 0 ? "answer" : "citation",
            source_surface: index % 3 === 0 ? "chat" : "search",
            document_id: index % 2 === 0 ? null : "doc-1",
            chunk_id: index % 2 === 0 ? null : `doc-1:${index + 1}`,
            message_id: index % 3 === 0 ? `msg-${index + 2}` : null,
            rating: index < 5 ? "not_helpful" : "helpful",
            reason: index < 5 ? (index % 2 === 0 ? "incomplete" : "not_relevant") : null,
            comment: null,
            created_at: `2026-07-01T0${7 - Math.min(index, 7)}:00:00Z`,
            conversation_id: index % 3 === 0 ? "conv-1" : null,
            conversation_title: null,
            model: index % 2 === 0 ? "oci.generativeai.command-r-plus" : null,
            file_name: index % 2 === 0 ? null : "経費規程.pdf",
            question_preview: `補足のフィードバック ${index + 1}`,
            comment_preview: null,
            has_comment: false,
          })),
        ],
        total: 120,
        limit,
        offset,
        has_next: offset + limit < 120,
      },
    },
    error_messages: [],
    warning_messages: [],
  };
}

function feedbackDetailEnvelope() {
  return {
    data: {
      feedback_id: "feedback-answer",
      trace_id: "trace-answer-123456789",
      business_view_id: "bv-1",
      business_view_name: "経理ビュー",
      target_type: "citation",
      source_surface: "chat",
      document_id: "doc-1",
      chunk_id: "doc-1:0",
      message_id: "msg-1",
      rating: "not_helpful",
      reason: "incorrect",
      comment: "規程が更新されています。",
      created_at: "2026-07-01T09:00:00Z",
      conversation_id: "conv-1",
      conversation_title: "経費申請",
      model: "oci.generativeai.command-r-plus",
      file_name: "経費規程.pdf",
      question_preview: "最新の経費申請期限を教えて",
      comment_preview: "規程が更新されています。",
      has_comment: true,
      content_source: "chat_message",
      question: "最新の経費申請期限を教えて",
      answer: "2024年版の経費規程では翌月10日です。",
      citations: [
        {
          document_id: "doc-1",
          chunk_id: "doc-1:0",
          file_name: "経費規程.pdf",
          section_title: "申請期限",
          page_number: 3,
          content_preview: "経費申請は利用月の翌月5日までに行います。",
          rerank_score: 0.94,
        },
      ],
      execution: {
        outcome: "success",
        search_mode: "hybrid",
        elapsed_ms: 842,
        retrieved_count: 20,
        reranked_count: 5,
        citation_count: 1,
        guardrail_codes: [],
        config_fingerprint: "9f3a57a3d91bdaf0",
      },
    },
    error_messages: [],
    warning_messages: [],
  };
}
