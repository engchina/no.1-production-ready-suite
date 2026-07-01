import { expect, test } from "@playwright/test";

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

test("フィードバックを集計・絞り込みし、元の会話と引用へ移動できる", async ({ page }) => {
  const requestedUrls: string[] = [];
  await page.route("**/api/feedback**", async (route) => {
    requestedUrls.push(route.request().url());
    await route.fulfill({ json: feedbackEnvelope() });
  });

  await page.goto("/feedback");

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByText("67%", { exact: true })).toBeVisible();
  await expect(page.getByText("内容が正しくない", { exact: true })).toBeVisible();
  await expect(page.getByText("経理ビュー", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: "会話を開く" })).toHaveAttribute(
    "href",
    "/chat?business_view_id=bv-1&conversation_id=conv-1#message-msg-1"
  );
  await expect(page.getByRole("link", { name: "引用元を開く" })).toHaveAttribute(
    "href",
    "/documents/doc-1?chunk_id=doc-1%3A0"
  );

  await page.getByRole("button", { name: "7日" }).click();
  await page.getByRole("combobox", { name: "評価対象" }).click();
  await page.getByRole("option", { name: "引用", exact: true }).click();
  await expect
    .poll(() => requestedUrls.some((url) => url.includes("period_days=7") && url.includes("target_type=citation")))
    .toBe(true);
  await expectNoPageOverflow(page);
});

test("フィードバック画面は375pxでも横へはみ出さない", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route("**/api/feedback**", (route) => route.fulfill({ json: feedbackEnvelope() }));

  await page.goto("/feedback");

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByRole("link", { name: "会話を開く" })).toBeVisible();
  await expectNoPageOverflow(page);
});

function feedbackEnvelope() {
  return {
    data: {
      summary: {
        total: 3,
        helpful_count: 2,
        not_helpful_count: 1,
        helpful_rate: 0.6667,
        answer_total: 2,
        answer_helpful_rate: 0.5,
        citation_total: 1,
        citation_helpful_rate: 1,
        reason_counts: [{ reason: "incorrect", count: 1 }],
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
            rating: "not_helpful",
            reason: "incorrect",
            created_at: "2026-07-01T09:00:00Z",
            conversation_id: "conv-1",
            conversation_title: "経費申請",
            message_id: "msg-1",
            model: "model-a",
            file_name: null,
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
            rating: "helpful",
            reason: null,
            created_at: "2026-07-01T08:00:00Z",
            conversation_id: null,
            conversation_title: null,
            message_id: null,
            model: null,
            file_name: "経費規程.pdf",
          },
        ],
        total: 3,
        limit: 20,
        offset: 0,
        has_next: false,
      },
    },
    error_messages: [],
    warning_messages: [],
  };
}
