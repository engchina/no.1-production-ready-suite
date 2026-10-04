import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

/**
 * フィードバック管理（#968）: 履歴検索のフォーカス・数値欄のキーボード入力・Select AI feedback の登録先。
 */

function envelope(route: Route, data: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ data }) });
}

function feedbackItem(id: string, question: string) {
  return {
    id,
    question,
    engine: "select_ai",
    profile_id: "sales",
    profile_name: "売上",
    profile_category: "販売",
    generated_sql: "SELECT TOTAL_AMOUNT FROM SALES",
    executable_sql: "SELECT TOTAL_AMOUNT FROM SALES",
    feedback_rating: "good",
    feedback_comment: "",
    admin_feedback_rating: null,
    admin_feedback_content: "",
    created_at: "2026-07-19T00:00:00Z",
    feedback_updated_at: "2026-07-19T00:00:00Z",
    elapsed_ms: 120,
    training_status: "",
    training_example_id: "",
  };
}

interface MockState {
  feedbackQueries: string[];
  vectorIndexPayload: unknown;
  feedbackConfigPayload: unknown;
  adminReviewPayload: Record<string, unknown> | null;
}

async function mockFeedbackApi(page: Page, options: { feedbackDelayMs?: number } = {}) {
  const state: MockState = {
    feedbackQueries: [],
    vectorIndexPayload: null,
    feedbackConfigPayload: null,
    adminReviewPayload: null,
  };
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    envelope(route, {
      runtime: "oracle",
      // 先頭は対象の履歴（売上）とは別の業務の profile。
      profiles: [
        { name: "NL2SQL_HR_PROFILE", owner: "APP" },
        { name: "NL2SQL_SALES_PROFILE", owner: "APP" },
      ],
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/select-ai/feedback?**", (route) => {
    const profileName = new URL(route.request().url()).searchParams.get("profile_name") ?? "";
    return envelope(route, {
      runtime: "oracle",
      profile_name: profileName,
      index_name: `${profileName}_FEEDBACK_VECINDEX`,
      table_name: `${profileName}_FEEDBACK_VECINDEX$VECTAB`,
      items: [],
      total: 0,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/select-ai/feedback/vector-index", (route) => {
    state.vectorIndexPayload = route.request().postDataJSON();
    return envelope(route, { runtime: "oracle", executed: true, status: "updated", warnings: [] });
  });
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    envelope(route, {
      items: [{ id: "sales", name: "売上", category: "販売", description: "", archived: false }],
      total: 1,
    })
  );
  await page.route("**/api/nl2sql/feedback-config", (route) => {
    if (route.request().method() === "PATCH") {
      state.feedbackConfigPayload = route.request().postDataJSON();
      return envelope(route, state.feedbackConfigPayload);
    }
    return envelope(route, { similarity_threshold: 0.8, match_limit: 3 });
  });
  await page.route(/\/api\/nl2sql\/feedback\?.*$/, async (route) => {
    const q = new URL(route.request().url()).searchParams.get("q") ?? "";
    state.feedbackQueries.push(q);
    if (options.feedbackDelayMs) await new Promise((resolve) => setTimeout(resolve, options.feedbackDelayMs));
    return envelope(route, {
      items: [feedbackItem("hist-sales-1", q ? `${q} の推移を確認したい` : "売上の推移を確認したい")],
      total: 1,
      next_cursor: "",
    });
  });
  await page.route("**/api/nl2sql/feedback/admin-review", (route) => {
    state.adminReviewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return envelope(route, {
      history_id: "hist-sales-1",
      rating: state.adminReviewPayload.rating,
      saved: true,
      feedback_content: state.adminReviewPayload.feedback_content,
      similar_history_publish: { history_id: "hist-sales-1", status: "published", runtime: "oracle", warnings: [] },
      select_ai_feedback: {
        runtime: "oracle",
        executed: true,
        status: "added",
        profile_name: "NL2SQL_SALES_PROFILE",
        index_name: "",
        table_name: "",
        sql_text: "select ai showsql 売上の推移を確認したい",
        stored_feedback_type: "NEGATIVE",
        plsql_preview: "",
        warnings: [],
        engine_meta: {},
      },
    });
  });
  return state;
}

test("履歴検索は一覧の読み込み中もフォーカスを保ち、続けて入力できる (#968)", async ({ page }) => {
  const state = await mockFeedbackApi(page, { feedbackDelayMs: 600 });
  await page.goto("/feedback-management?tab=appFeedback");
  const rows = page.getByTestId("feedback-history-row");
  await expect(rows).toHaveCount(1);

  const search = page.getByTestId("feedback-app-filters").getByRole("searchbox", { name: "履歴検索" });
  await search.click();
  await page.keyboard.type("売上");
  // debounce の後に一覧の読み込みが始まる。読み込み中も検索欄は操作でき、フォーカスが残る。
  await expect(page.getByTestId("app-feedback-load-processing")).toBeVisible();
  await expect(search).toBeEnabled();
  await expect(search).toBeFocused();
  // 読み込み中は一覧の行と編集欄を操作させない（読み込みの後に対象の履歴が変わることがあるため）。
  await expect(rows.first()).toBeDisabled();
  await expect(page.getByRole("button", { name: "フィードバック保存" })).toBeDisabled();

  await page.keyboard.type("額");
  await expect(search).toHaveValue("売上額");
  await expect(rows).toContainText("売上額 の推移を確認したい");
  await expect(search).toBeFocused();
  expect(state.feedbackQueries.at(-1)).toBe("売上額");
  await expect(page.getByRole("button", { name: "フィードバック保存" })).toBeEnabled();
});

test("ベクトルインデックスと類似検索の数値欄にキーボードで値を入力できる (#968)", async ({ page }) => {
  const state = await mockFeedbackApi(page);
  await page.goto("/feedback-management?tab=vectorIndex");

  const threshold = page.getByLabel("Similarity_Threshold", { exact: true });
  await threshold.click();
  await threshold.press("ControlOrMeta+a");
  await page.keyboard.type("0.5");
  await expect(threshold).toHaveValue("0.5");

  const matchLimit = page.getByLabel("Match_Limit", { exact: true });
  await matchLimit.click();
  await matchLimit.press("ControlOrMeta+a");
  await page.keyboard.press("Backspace");
  // 空の間は最小値に置き換えない。
  await expect(matchLimit).toHaveValue("");
  await page.keyboard.type("4");
  await expect(matchLimit).toHaveValue("4");
  await page.getByRole("button", { name: "ベクトルインデックスを更新" }).click();
  await expect.poll(() => state.vectorIndexPayload).toEqual({
    profile_name: "NL2SQL_HR_PROFILE",
    similarity_threshold: 0.5,
    match_limit: 4,
  });

  // 範囲外の値は確定せず、欄を離れたら確定した値の表示に戻す。
  await threshold.click();
  await threshold.press("ControlOrMeta+a");
  await page.keyboard.type("3");
  await expect(threshold).toHaveValue("3");
  await matchLimit.click();
  await expect(threshold).toHaveValue("0.5");

  await page.getByRole("tab", { name: "類似検索インデックス" }).click();
  const similarityMatchLimit = page.locator("#feedback-similarity-match-limit");
  await similarityMatchLimit.click();
  await similarityMatchLimit.press("ControlOrMeta+a");
  await page.keyboard.press("Backspace");
  await expect(similarityMatchLimit).toHaveValue("");
  await page.keyboard.type("12");
  await expect(similarityMatchLimit).toHaveValue("12");
  await page.getByRole("button", { name: "設定保存" }).click();
  await expect.poll(() => state.feedbackConfigPayload).toEqual({ similarity_threshold: 0.8, match_limit: 12 });
});

test("管理者レビューからの Select AI feedback の登録先は対象の履歴の業務プロファイルで決める (#968)", async ({
  page,
}, testInfo) => {
  const state = await mockFeedbackApi(page);
  await page.goto("/feedback-management?tab=appFeedback");
  await expect(page.getByTestId("app-feedback-selected-question")).toContainText("売上の推移を確認したい");

  const register = page.getByRole("checkbox", { name: "Select AI feedback に登録する" });
  await expect(register).toHaveAccessibleDescription(
    "対象の履歴の業務プロファイルの Select AI profile に登録します。"
  );
  await register.check();
  await register.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("feedback-register-select-ai.png") });
  await page.getByRole("button", { name: "フィードバック保存" }).click();
  await expect(page.getByText("管理者レビューを保存し、Select AI feedback に登録しました。")).toBeVisible();
  // 「Select AI feedback」タブで選んでいる profile（先頭の NL2SQL_HR_PROFILE）を送らない。
  expect(state.adminReviewPayload).toMatchObject({
    history_id: "hist-sales-1",
    register_select_ai_feedback: true,
    select_ai_profile_name: "",
  });
});
