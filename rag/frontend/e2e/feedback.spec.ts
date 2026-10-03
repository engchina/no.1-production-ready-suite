import { expect, test, type Page, type Route } from "@playwright/test";

import {
  expectedControlHeight,
  expectNoPageOverflow,
  measureTableCellOverflow,
  mockAuthUser,
  mockDatabaseReady,
  mockLocalAuth,
} from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/search-answer-profiles**", (route) =>
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
    "/chat?search_answer_profile_id=bv-1&conversation_id=conv-1#message-msg-1"
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

test("行の題名のボタン（共有 RowTitleButton）は Tab で届き、Enter / Space で選び、選んだ対象を aria-current で伝える", async ({ page }, testInfo) => {
  // #421: RAG の EntityLayout の RowTitleButton を packages/ui の共有部品へ移した。
  await mockFeedback(page, []);
  await page.goto("/feedback?period=30&sort=newest&size=25&page=1");
  await expect(page.getByRole("heading", { name: "利用者フィードバック" })).toBeVisible();

  const answerButton = page.getByRole("button", { name: "最新の経費申請期限を教えて の詳細を表示" });
  const citationButton = page.getByRole("button", { name: "申請期限を確認したい の詳細を表示" });
  await expect(answerButton).toBeVisible();
  await expect(answerButton).toHaveAttribute("data-row-title-button", "");
  await expect(answerButton).not.toHaveAttribute("aria-current", /.*/);

  // Tab で届く（行そのものはフォーカスを受けず、題名のボタンが選ぶ導線）。
  await answerButton.focus();
  await page.keyboard.press("Shift+Tab");
  await page.keyboard.press("Tab");
  await expect(answerButton).toBeFocused();
  // フォーカスの表示は outline 1 つ（2px、ring なし。#355）。
  const focusStyle = await answerButton.evaluate((node) => {
    const style = getComputedStyle(node);
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth, boxShadow: style.boxShadow };
  });
  expect(focusStyle.outlineStyle).toBe("solid");
  expect(focusStyle.outlineWidth).toBe("2px");
  expect(focusStyle.boxShadow).toBe("none");

  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/feedback=feedback-answer/);
  await expect(answerButton).toHaveAttribute("aria-current", "true");

  await citationButton.focus();
  await page.keyboard.press("Space");
  await expect(page).toHaveURL(/feedback=feedback-citation/);
  await expect(citationButton).toHaveAttribute("aria-current", "true");
  await expect(answerButton).not.toHaveAttribute("aria-current", /.*/);

  if (testInfo.project.name === "desktop") {
    // desktop は表の行と題名のボタンの両方が「現在の項目」。ホバーで題名に下線を引く。
    await expect(page.getByTestId("feedback-row-feedback-citation")).toHaveAttribute("aria-current", "true");
    await answerButton.hover();
    await expect(answerButton.locator("span").first()).toHaveCSS("text-decoration-line", "underline");
  } else {
    // 375px のカード一覧（Pixel 5 = タッチ端末）: カードと題名のボタンが「現在の項目」。
    await expect(page.locator("main li[aria-current='true']")).toContainText("申請期限を確認したい");
    // タッチ端末では見た目を変えずに当たり判定を縦 44px 以上に広げる（pr-touch-target、#364）。
    const hit = await answerButton.evaluate((node) => {
      node.scrollIntoView({ block: "center" });
      const rect = node.getBoundingClientRect();
      const reach = Math.max(0, (44 - rect.height) / 2) - 1;
      const above = document.elementFromPoint(rect.left + rect.width / 2, rect.top - reach);
      return { height: rect.height, hitsSelf: reach <= 0 || node.contains(above) };
    });
    expect(hit.hitsSelf).toBe(true);
    expect(hit.height).toBeLessThan(44);
    await expectNoPageOverflow(page);
  }
  await page.screenshot({ path: testInfo.outputPath(`feedback-row-title-${testInfo.project.name}.png`), fullPage: true });
});

test("回答のフィードバックを詳細から Approved FAQ に登録し、品質評価のケースに追加できる", async ({ page }) => {
  await mockFeedback(page, []);
  await page.route("**/api/feedback/feedback-answer", (route) => {
    const envelope = feedbackDetailEnvelope();
    return route.fulfill({
      json: {
        ...envelope,
        data: {
          ...envelope.data,
          target_type: "answer",
          document_id: null,
          chunk_id: null,
          corrected_answer: "2026年版の経費規程では翌月末です。",
        },
      },
    });
  });
  let promoted = false;
  await page.route("**/api/feedback/feedback-answer/approved-faq", async (route) => {
    promoted = route.request().method() === "POST";
    await route.fulfill({
      json: {
        data: { search_answer_profile_id: "bv-1", question: "最新の経費申請期限を教えて", inserted_count: 1, deleted_count: 0 },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/feedback/feedback-answer/evaluation-case", (route) =>
    route.fulfill({
      json: {
        data: {
          id: "feedback-feedback-answer",
          query: "最新の経費申請期限を教えて",
          relevant_document_ids: [],
          expected_answer_keywords: ["翌月末"],
        },
        error_messages: [],
        warning_messages: [],
      },
    })
  );

  await page.goto("/feedback?period=30&feedback=feedback-answer");
  const detail = page.getByRole("region", { name: "フィードバック詳細" });
  await expect(detail.getByRole("heading", { name: "修正した回答" })).toBeVisible();
  const actions = detail.getByTestId("feedback-detail-actions");

  await actions.getByRole("button", { name: "Approved FAQ に登録" }).click();
  const confirmDialog = page.getByRole("alertdialog", { name: "検索・回答プロファイルの Approved FAQ に登録しますか？" });
  await confirmDialog.getByRole("button", { name: "Approved FAQ に登録" }).click();
  await expect(page.getByText("Approved FAQ に登録しました: 最新の経費申請期限を教えて")).toBeVisible();
  expect(promoted).toBe(true);

  await actions.getByRole("button", { name: "品質評価のケースに追加" }).click();
  await expect(page).toHaveURL(/\/evaluation$/);
  const stored = await page.evaluate(() =>
    Object.entries(sessionStorage).find(([key]) => key.includes("evaluation.requestJson"))?.[1] ?? null
  );
  expect(stored).toContain("feedback-feedback-answer");
  expect(stored).toContain("翌月末");
});

test("承認 FAQ への反映の権限が無い利用者には Approved FAQ への登録を出さない", async ({ page }) => {
  await mockFeedback(page, []);
  // フィードバックの閲覧だけを持つ DB ユーザー（rag.feedback.manage なし。#214）。
  await mockAuthUser(page, {
    permissions: ["menu.feedback", "menu.evaluation"],
    allowed_search_answer_profile_ids: ["bv-1"],
  });
  await page.route("**/api/feedback/feedback-answer", (route) => {
    const envelope = feedbackDetailEnvelope();
    return route.fulfill({
      json: { ...envelope, data: { ...envelope.data, target_type: "answer", document_id: null, chunk_id: null } },
    });
  });

  await page.goto("/feedback?period=30&feedback=feedback-answer");
  const detail = page.getByRole("region", { name: "フィードバック詳細" });
  const actions = detail.getByTestId("feedback-detail-actions");
  await expect(actions.getByRole("button", { name: "品質評価のケースに追加" })).toBeVisible();
  await expect(actions.getByRole("button", { name: "Approved FAQ に登録" })).toHaveCount(0);

  // 文書の詳細を開けない利用者には、根拠から引用元（文書の詳細）へのリンクを出さない（#303）。
  await detail.getByRole("tab", { name: "根拠" }).click();
  const evidence = page.getByRole("tabpanel", { name: "根拠" });
  await expect(evidence.locator("article").first()).toBeVisible();
  await expect(evidence.getByRole("link", { name: "引用元を開く" })).toHaveCount(0);
});

test("見える範囲を案内する: SYSTEM_ADMIN はすべての利用者の分、ほかのロールは自分が送った分だけ", async ({ page }) => {
  // 範囲は backend が SQL で絞る（#408）。画面は範囲の案内を、集計・一覧より前に出す。
  await mockFeedback(page, []);
  await page.goto("/feedback?period=30&sort=newest&size=50&page=1");
  const allNotice = page.getByRole("status").filter({ hasText: "すべての利用者のフィードバックを表示しています" });
  await expect(allNotice).toBeVisible();
  await expect(page.getByText("自分が送ったフィードバックだけを表示しています")).toHaveCount(0);
  await expectNoPageOverflow(page);

  // SYSTEM_ADMIN 以外のロール（承認 FAQ への反映の権限を持っていても同じ）。後の route が優先する。
  await mockAuthUser(page, {
    permissions: ["menu.feedback", "rag.feedback.manage"],
    allowed_search_answer_profile_ids: ["bv-1"],
  });
  await page.reload();
  const ownNotice = page.getByRole("status").filter({ hasText: "自分が送ったフィードバックだけを表示しています" });
  await expect(ownNotice).toBeVisible();
  await expect(ownNotice).toContainText("SYSTEM_ADMIN のロールの利用者だけが確認できます");
  await expect(page.getByText("すべての利用者のフィードバックを表示しています")).toHaveCount(0);
  const noticeBox = await ownNotice.boundingBox();
  const summaryBox = await page.getByText("全体の有用率", { exact: true }).first().boundingBox();
  expect(noticeBox !== null && summaryBox !== null && noticeBox.y < summaryBox.y).toBe(true);
  await expectNoPageOverflow(page);
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
            search_answer_profile_id: "bv-1",
            search_answer_profile_name: "経理ビュー",
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
            search_answer_profile_id: "bv-1",
            search_answer_profile_name: "経理ビュー",
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
            // 列幅の検査用: 最も長い対象 / 送信元・理由・検索・回答プロファイル名・モデル名
            feedback_id: "feedback-legacy-long",
            trace_id: "trace-legacy-long",
            search_answer_profile_id: "bv-2",
            search_answer_profile_name: "経理・財務・監査の横断ナレッジ検索・回答プロファイル",
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
            search_answer_profile_id: "bv-1",
            search_answer_profile_name: "経理ビュー",
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
      search_answer_profile_id: "bv-1",
      search_answer_profile_name: "経理ビュー",
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

// #384: 検索欄は共有の TextField（先頭アイコン・クリア）。高さはコントロールの高さのトークン（--field-height）。
test("検索欄は隣の SelectField と同じ高さで、先頭アイコン・クリアボタン・Escape で入力を扱える", async ({ page }, testInfo) => {
  await mockFeedback(page, []);
  await page.goto("/feedback?period=30&sort=newest&size=50&page=1");

  const search = page.getByRole("searchbox", { name: "問題・回答・コメントを検索" });
  await expect(search).toBeVisible();
  const select = page.getByRole("combobox", { name: "検索・回答プロファイル" });
  const [searchBox, selectBox] = await Promise.all([search.boundingBox(), select.boundingBox()]);
  expect(searchBox && selectBox).toBeTruthy();
  // 入力欄・選択欄は md（36px）。タッチ端末では Button と同じく 44px（#613）。
  expect(Math.round(searchBox!.height)).toBe(await expectedControlHeight(page));
  expect(Math.abs(searchBox!.height - selectBox!.height)).toBeLessThanOrEqual(0.5);
  // 選択欄 5 つが 1 行目、検索欄は 2 行目（#405）。検索欄は選択欄の行の下にあり、左端がそろう。
  expect(searchBox!.y).toBeGreaterThan(selectBox!.y + selectBox!.height);
  expect(Math.abs(searchBox!.x - selectBox!.x)).toBeLessThanOrEqual(0.5);
  if (testInfo.project.name === "desktop") {
    // desktop（1280px）では選択欄 5 つが 1 行に並び、値が省略されない幅を持つ。
    const lastSelect = page.getByRole("combobox", { name: "並び順" });
    const lastBox = await lastSelect.boundingBox();
    expect(Math.abs(lastBox!.y - selectBox!.y)).toBeLessThanOrEqual(0.5);
    // Tab の順も見た目の順と同じ: 並び順の次に検索欄へ移る。
    await lastSelect.focus();
    await page.keyboard.press("Tab");
    await expect(search).toBeFocused();
    await search.blur();
  }
  // 角丸は Button・SelectField と同じ --radius-control（6px）。
  expect(await search.evaluate((node) => getComputedStyle(node).borderTopLeftRadius)).toBe("6px");
  expect(await select.evaluate((node) => getComputedStyle(node).borderTopLeftRadius)).toBe("6px");

  // 先頭アイコンは 16px・読み上げない。押すと入力欄にフォーカスが入る（ポインタを透過する）。
  const field = search.locator("xpath=..");
  const icon = field.locator('[data-text-field-slot="leading"]');
  await expect(icon).toHaveAttribute("aria-hidden", "true");
  const iconBox = await icon.boundingBox();
  expect(Math.round(iconBox!.width)).toBe(16);
  const paddingLeft = await search.evaluate((node) => Number.parseFloat(getComputedStyle(node).paddingLeft));
  expect(paddingLeft).toBeGreaterThanOrEqual(iconBox!.x + iconBox!.width - searchBox!.x + 7);
  await icon.click({ force: true });
  await expect(search).toBeFocused();

  // 値が空のときはクリアボタンを出さない。入力すると入力欄の中の右端に出て、文字と重ならない。
  const clear = page.getByRole("button", { name: "検索語をクリア" });
  await expect(clear).toHaveCount(0);
  await search.fill("申請期限");
  await expect(clear).toBeVisible();
  const [clearBox, filledBox] = await Promise.all([clear.boundingBox(), search.boundingBox()]);
  expect(clearBox!.x + clearBox!.width).toBeLessThanOrEqual(filledBox!.x + filledBox!.width);
  expect(clearBox!.y).toBeGreaterThanOrEqual(filledBox!.y);
  expect(clearBox!.y + clearBox!.height).toBeLessThanOrEqual(filledBox!.y + filledBox!.height);
  const paddingRight = await search.evaluate((node) => Number.parseFloat(getComputedStyle(node).paddingRight));
  expect(paddingRight).toBeGreaterThanOrEqual(clearBox!.width);
  // ブラウザ既定の type=search のクリア（×）は出さない（共有のクリアボタンと二重にしない）。
  // getComputedStyle は ::-webkit-search-cancel-button を解決しないため、入力欄に当たる規則を CSSOM で確かめる。
  expect(
    await search.evaluate((node) => {
      const rules: CSSStyleRule[] = [];
      const collect = (list: CSSRuleList) => {
        for (const rule of Array.from(list)) {
          if (rule instanceof CSSStyleRule) {
            rules.push(rule);
            if (rule.cssRules.length) collect(rule.cssRules);
          } else if ("cssRules" in rule) collect((rule as CSSGroupingRule).cssRules);
        }
      };
      for (const sheet of Array.from(document.styleSheets)) collect(sheet.cssRules);
      return rules.some((rule) => {
        const style = rule.style.getPropertyValue("appearance");
        const text = rule.selectorText ?? "";
        if (style !== "none" || !text.includes("::-webkit-search-cancel-button")) return false;
        // ネストした規則（&::-webkit-search-cancel-button）は親のクラスで入力欄に当たるかを見る。
        const parent = rule.parentRule instanceof CSSStyleRule ? rule.parentRule.selectorText : text.split("::")[0];
        return node.matches(parent);
      });
    })
  ).toBe(true);

  // Tab 順は入力欄 → クリアボタン。押すと値を消して入力欄に戻る。
  await search.focus();
  await page.keyboard.press("Tab");
  await expect(clear).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(search).toHaveValue("");
  await expect(search).toBeFocused();
  await expect(clear).toHaveCount(0);

  // ポインタで押しても入力欄からフォーカスを外さない。
  await search.fill("経費");
  await clear.click();
  await expect(search).toHaveValue("");
  await expect(search).toBeFocused();

  // Escape でも消える（値があるときだけ）。
  await search.fill("経費");
  await search.press("Escape");
  await expect(search).toHaveValue("");
  await expectNoPageOverflow(page);
  await page.screenshot({ path: testInfo.outputPath(`feedback-search-${testInfo.project.name}.png`) });
});

// #374: 強制カラーモードで、選ばれていないタブに下線を出さない（選んだタブだけ Highlight の下線）。
test("強制カラーモードでは、選んだタブだけに Highlight の下線を出す", async ({ page }) => {
  await mockFeedback(page, []);
  await page.emulateMedia({ forcedColors: "active" });
  await page.goto("/feedback?period=30&sort=newest&size=50&page=1&feedback=feedback-answer");

  const detail = page.getByRole("region", { name: "フィードバック詳細" });
  const selected = detail.getByRole("tab", { name: "内容" });
  const unselected = detail.getByRole("tab", { name: "根拠" });
  await expect(selected).toHaveAttribute("aria-selected", "true");
  await expect(unselected).toHaveAttribute("aria-selected", "false");

  const systemColor = (name: string) =>
    page.evaluate((color) => {
      const probe = document.createElement("div");
      probe.style.color = color;
      document.body.append(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      return value;
    }, name);
  const [canvas, canvasText, highlight] = await Promise.all([
    systemColor("Canvas"),
    systemColor("CanvasText"),
    systemColor("Highlight"),
  ]);
  const underline = (tab: typeof selected) => tab.evaluate((node) => getComputedStyle(node).borderBottomColor);

  // 選ばれていないタブの透明の下線は CanvasText に塗られず、背景と同じ Canvas になる。
  expect(await underline(unselected)).toBe(canvas);
  expect(await underline(unselected)).not.toBe(canvasText);
  // 選んだタブは Highlight の下線と太字で区別する。
  expect(await underline(selected)).toBe(highlight);
  expect(Number(await selected.evaluate((node) => getComputedStyle(node).fontWeight))).toBeGreaterThan(
    Number(await unselected.evaluate((node) => getComputedStyle(node).fontWeight))
  );

  // 選び直すと、下線も移る。
  await unselected.click();
  await expect(unselected).toHaveAttribute("aria-selected", "true");
  // 色の transition の途中の値は system color ではないため CanvasText に置き換わる。終わるのを待つ。
  await expect.poll(() => underline(unselected)).toBe(highlight);
  await expect.poll(() => underline(selected)).toBe(canvas);
});
