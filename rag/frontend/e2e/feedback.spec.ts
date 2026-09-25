import { expect, test, type Page, type Route } from "@playwright/test";

import { expectNoPageOverflow, measureTableCellOverflow, mockDatabaseReady } from "./_helpers";

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

test("高密度一覧を検索・数値ページングし、行を選ぶと分割ペインの詳細の三つのタブから原因を追える", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop table contract");
  const requestedUrls: string[] = [];
  await mockFeedback(page, requestedUrls);

  await page.goto("/feedback?period=30&sort=newest&size=50&page=1");

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "問題の概要" })).toBeVisible();
  await expect(page.getByRole("table").getByText("最新の経費申請期限を教えて", { exact: true })).toBeVisible();
  await expect(page.getByText("前期間比 +17pt", { exact: true }).first()).toBeVisible();
  // B 型：一覧と詳細を FixedSplitPane で並べ、未選択の詳細は選び方を案内する（#147）。
  const split = page.getByTestId("fixed-split-pane-feedback-list");
  await expect(split).toHaveAttribute("data-split-layout", "split");
  await expect(page.getByText("フィードバックを選んでください")).toBeVisible();
  // 「詳細を開く」ボタンは行に置かない（行のクリックと名前のボタンで選ぶ）。
  await expect(page.getByRole("button", { name: "詳細を開く" })).toHaveCount(0);

  await page.getByRole("searchbox", { name: "問題・回答・コメントを検索" }).fill("申請期限");
  await expect.poll(() => requestedUrls.some((url) => url.includes("q=%E7%94%B3%E8%AB%8B%E6%9C%9F%E9%99%90"))).toBe(true);

  await page.getByRole("button", { name: "2ページへ移動" }).click();
  await expect(page).toHaveURL(/page=2/);
  await expect.poll(() => requestedUrls.some((url) => url.includes("offset=50"))).toBe(true);

  await page.getByRole("combobox", { name: "1ページの表示件数" }).click();
  await page.getByRole("option", { name: "25件" }).click();
  await expect(page).toHaveURL(/size=25/);
  await expect(page).toHaveURL(/page=1/);

  // 行の操作以外の領域（理由の列）のクリックで選び、選んだ行を aria-current と背景で示す。
  const row = page.getByTestId("feedback-row-feedback-answer");
  await row.getByText("内容が正しくない").click();
  await expect(page).toHaveURL(/feedback=feedback-answer/);
  await expect(row).toHaveAttribute("aria-current", "true");
  const detail = page.getByRole("region", { name: "フィードバック詳細" });
  await expect(detail).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(detail.getByText("2024年版の経費規程では翌月10日です。", { exact: true })).toBeVisible();
  await expect(detail.getByText("規程が更新されています。", { exact: true })).toBeVisible();
  // 横並びでは選んだ行にフォーカスを残し、詳細は右にすぐ出る。
  const box = await detail.boundingBox();
  const rowBox = await row.boundingBox();
  expect(box && rowBox && box.x > rowBox.x).toBe(true);

  await detail.getByRole("tab", { name: "根拠" }).click();
  await expect(page.getByRole("tabpanel", { name: "根拠" }).getByText("評価対象", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "引用元を開く" })).toHaveAttribute(
    "href",
    "/documents/doc-1?chunk_id=doc-1%3A0"
  );

  await detail.getByRole("tab", { name: "実行情報" }).click();
  await expect(
    page.getByRole("tabpanel", { name: "実行情報" }).getByText("oci.generativeai.command-r-plus", { exact: true })
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "会話を開く" })).toHaveAttribute(
    "href",
    "/chat?business_view_id=bv-1&conversation_id=conv-1#message-msg-1"
  );

  await page.screenshot({ path: testInfo.outputPath("feedback-root-cause-desktop.png"), fullPage: true });

  // 再読込しても URL から同じ行を選んだ状態に戻る。
  await page.reload();
  await expect(page.getByTestId("feedback-row-feedback-answer")).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("region", { name: "フィードバック詳細" })).toBeVisible();

  // キーボード：先頭セルの名前のボタンで別の行を選ぶ。
  const citationButton = page.getByRole("button", { name: "申請期限を確認したい の詳細を表示" });
  await citationButton.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/feedback=feedback-citation/);
  await expect(page.getByTestId("feedback-row-feedback-citation")).toHaveAttribute("aria-current", "true");
  await expect(page.getByTestId("feedback-row-feedback-answer")).not.toHaveAttribute("aria-current", "true");
  await expect(citationButton).toBeFocused();

  // 詳細を閉じると選択を外し、行の名前のボタンへフォーカスを戻す。
  await page.getByRole("region", { name: "フィードバック詳細" }).getByRole("button", { name: "詳細を閉じる" }).click();
  await expect(page).not.toHaveURL(/feedback=/);
  await expect(citationButton).toBeFocused();
  await expect(page.getByText("フィードバックを選んでください")).toBeVisible();

  // divider は role="separator" で、矢印キーで比率を変えると製品の key で保存する。
  const divider = page.getByRole("separator", { name: "一覧と詳細の表示比率" });
  const before = Number(await divider.getAttribute("aria-valuenow"));
  await divider.focus();
  await page.keyboard.press("ArrowLeft");
  await expect.poll(async () => Number(await divider.getAttribute("aria-valuenow"))).toBeLessThan(before);
  const stored = await page.evaluate(() =>
    window.localStorage.getItem("production-ready-rag.fixedSplitPane.feedback-list")
  );
  expect(stored).not.toBeNull();
  await expectNoPageOverflow(page);
});

test("375pxでは一覧と詳細を縦に積み、カードを選ぶと詳細へ移り、URLから選択を復元できる", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile card contract");
  await page.setViewportSize({ width: 375, height: 812 });
  await mockFeedback(page, []);

  await page.goto(
    "/feedback?period=7&target=answer&rating=not_helpful&reason=incorrect&sort=newest&size=25&page=1&feedback=feedback-answer"
  );

  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "問題の概要" })).toBeHidden();
  await expect(page.getByTestId("fixed-split-pane-feedback-list")).toHaveAttribute("data-split-layout", "stacked");
  const selectedCard = page.locator("main li[aria-current='true']");
  await expect(selectedCard).toHaveCount(1);
  await expect(selectedCard).toContainText("最新の経費申請期限を教えて");
  const detail = page.getByRole("region", { name: "フィードバック詳細" });
  await expect(detail).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // 縦積みでは詳細が一覧の下に来る。
  const detailBox = await detail.boundingBox();
  const cardBox = await selectedCard.boundingBox();
  expect(detailBox && cardBox && detailBox.y > cardBox.y).toBe(true);

  await detail.getByRole("tab", { name: "根拠" }).click();
  await expect(page.getByText("経費規程.pdf", { exact: true }).last()).toBeVisible();
  await detail.getByRole("button", { name: "詳細を閉じる" }).click();
  await expect(page).not.toHaveURL(/feedback=/);
  await expect(page.locator("main li[aria-current='true']")).toHaveCount(0);

  // カードの操作以外の領域を押すと選び、縦積みでは詳細の見出しへフォーカスを移す。
  const card = page.locator("main li").filter({ hasText: "申請期限を確認したい" });
  await card.getByText("引用 / RAG検索").click();
  await expect(page).toHaveURL(/feedback=feedback-citation/);
  await expect(card).toHaveAttribute("aria-current", "true");
  await expect(page.getByRole("heading", { name: "フィードバック詳細" })).toBeFocused();
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath("feedback-root-cause-mobile.png"), fullPage: true });
});

for (const width of [1280, 1920]) {
  test(`明細表の値は列の境界を超えず、次の列に重ならない (${width}px)`, async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "desktop table contract");
    await page.setViewportSize({ width, height: 900 });
    await mockFeedback(page, []);
    await page.goto("/feedback?period=30&sort=newest&size=50&page=1");

    const table = page.getByRole("table");
    await expect(table.getByText("役に立たなかった").first()).toBeVisible();
    await expect(table.getByText("引用 / 不明（旧データ）")).toBeVisible();
    expect(await measureTableCellOverflow(page, "main table")).toEqual([]);

    // 評価は状態なのでサムズアップ / ダウンのアイコンを持ち、評価対象などのタグはアイコンを持たない
    const notHelpful = table.locator("[data-status-variant='danger']").first();
    await expect(notHelpful).toHaveText("役に立たなかった");
    await expect(notHelpful.locator("svg")).toHaveCount(1);
    await expectNoPageOverflow(page);
  });
}

test("375pxのカード一覧は評価バッジや値がカードの外にはみ出さない", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "mobile card contract");
  await page.setViewportSize({ width: 375, height: 812 });
  await mockFeedback(page, []);
  await page.goto("/feedback?period=30&sort=newest&size=50&page=1");

  const cards = page.locator("main li").filter({ hasText: /役に立/ });
  await expect(cards.first()).toBeVisible();
  const overflow = await cards.evaluateAll((items) =>
    items.flatMap((item) => {
      const box = item.getBoundingClientRect();
      return Array.from(item.querySelectorAll("*"))
        .filter((el) => {
          const rect = el.getBoundingClientRect();
          return rect.width > 1 && (rect.right > box.right + 0.5 || rect.left < box.left - 0.5);
        })
        .map((el) => (el.textContent ?? el.tagName).trim().slice(0, 24));
    })
  );
  expect(overflow).toEqual([]);
  await expectNoPageOverflow(page);
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
  if (url.pathname === "/api/feedback/feedback-citation") {
    const envelope = feedbackDetailEnvelope();
    await route.fulfill({
      json: { ...envelope, data: { ...envelope.data, feedback_id: "feedback-citation", rating: "helpful", reason: null } },
    });
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
          {
            // 列幅の検査用: 最も長い対象 / 送信元・理由・業務ビュー名・モデル名
            feedback_id: "feedback-legacy-long",
            trace_id: "trace-legacy-long",
            business_view_id: "bv-2",
            business_view_name: "経理・財務・監査の横断ナレッジ業務ビュー",
            target_type: "citation",
            source_surface: null,
            document_id: "doc-2",
            chunk_id: "doc-2:0",
            message_id: null,
            rating: "not_helpful",
            reason: "missing_evidence",
            comment: null,
            created_at: "2026-06-30T23:59:00Z",
            conversation_id: null,
            conversation_title: null,
            model: "ocid1.generativeaiendpoint.oc1.ap-osaka-1.amaaaaaaexample",
            file_name: "経費規程.pdf",
            question_preview: "旧データの評価（送信元が記録されていない）",
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
