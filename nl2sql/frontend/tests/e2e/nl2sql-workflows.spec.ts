import { expect, test, type Page, type Route } from "@playwright/test";

type JsonValue = Record<string, unknown> | unknown[];

interface MockApiState {
  previewPayload: Record<string, unknown> | null;
  executePayload: Record<string, unknown> | null;
}

const safety = {
  is_safe: true,
  is_select_only: true,
  row_limit_applied: 100,
  blocked_reason: "",
  warnings: [],
  referenced_tables: ["INVOICES"],
  referenced_columns: ["TOTAL_AMOUNT"],
};

const timing = {
  created_at: "2026-06-21T10:00:00.000Z",
  started_at: "2026-06-21T10:00:00.010Z",
  finished_at: "2026-06-21T10:00:00.180Z",
  elapsed_ms: 170,
  stage_timings: [{ stage: "mock", elapsed_ms: 170 }],
};

const schemaCatalog = {
  refreshed_at: "2026-06-21T10:00:00.000Z",
  tables: [
    {
      table_name: "INVOICES",
      logical_name: "請求",
      owner: "APP",
      table_type: "TABLE",
      comment: "請求情報",
      row_count: 2,
      constraints: ["PK_INVOICES"],
      columns: [
        {
          column_name: "CUSTOMER_NAME",
          logical_name: "取引先名",
          data_type: "VARCHAR2(120)",
          nullable: false,
          comment: "取引先名",
          sample_values: ["青山商事"],
        },
        {
          column_name: "TOTAL_AMOUNT",
          logical_name: "請求金額",
          data_type: "NUMBER",
          nullable: false,
          comment: "税込請求金額",
          sample_values: ["1200000"],
        },
      ],
    },
  ],
};

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    description: "請求・顧客を扱う既定プロファイル",
    allowed_tables: ["INVOICES"],
    glossary: { 請求金額: "INVOICES.TOTAL_AMOUNT" },
    sql_rules: ["SELECT のみ"],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    archived: false,
  },
];

const historyItem = {
  id: "hist-001",
  question: "履歴から再実行したい請求金額",
  engine: "select_ai_agent",
  generated_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
  executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
  created_at: "2026-06-21T10:00:00.000Z",
  elapsed_ms: 210,
  feedback_rating: null,
  profile_id: "default",
  profile_name: "既定プロファイル",
  rewritten_question: "履歴から再実行したい請求金額",
  safety_is_safe: true,
  result_row_count: 1,
  result_columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
  feedback_comment: "",
};

function fulfillJson(route: Route, data: JsonValue) {
  return route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

async function mockNl2SqlApi(page: Page): Promise<MockApiState> {
  const state: MockApiState = {
    previewPayload: null,
    executePayload: null,
  };

  await page.route("**/api/schema/catalog", (route) => fulfillJson(route, schemaCatalog));
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
  await page.route("**/api/nl2sql/history", (route) => fulfillJson(route, { items: [historyItem] }));
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "default",
      recommended_profile_name: "既定プロファイル",
      confidence: 0.94,
      reason: "請求関連の語彙に一致しました。",
      rewritten_question: "請求金額を一覧で見たい",
      recommended_allowed_objects: {
        table_names: ["INVOICES"],
        columns: { INVOICES: ["TOTAL_AMOUNT"] },
      },
      candidates: [
        {
          profile_id: "default",
          profile_name: "既定プロファイル",
          score: 0.94,
          matched_terms: ["請求金額"],
          allowed_tables: ["INVOICES"],
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/similar-history", (route) =>
    fulfillJson(route, {
      items: [
        {
          item: historyItem,
          score: 0.9,
          reason: "請求金額の履歴と近い質問です。",
        },
      ],
    })
  );
  await page.route("**/api/nl2sql/preview", (route) => {
    state.previewPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
      is_safe: true,
      row_limit: 100,
      note: "mock preview",
      engine: "select_ai_agent",
      engine_meta: { profile: "mock_agent_profile" },
      fallback_reason: "",
      rewritten_question: "請求金額を一覧で見たい",
      executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
      safety,
      recommendations: ["許可された表だけを参照しています。"],
      repaired_sql: "",
      optimization_hints: ["TOTAL_AMOUNT に索引を検討できます。"],
      timing,
    });
  });
  await page.route("**/api/nl2sql/execute", (route) => {
    state.executePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
      rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
      total: 1,
    });
  });
  await page.route("**/api/nl2sql/compare", (route) =>
    fulfillJson(route, {
      question: "今月の請求金額が大きい取引先を表示して",
      recommendation: "Select AI Agent が最短で安全な SQL を生成しました。",
      results: [
        {
          sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES",
          is_safe: true,
          row_limit: 100,
          note: "agent",
          engine: "select_ai_agent",
          engine_meta: { team: "mock_team" },
          fallback_reason: "",
          rewritten_question: "今月の請求金額が大きい取引先",
          executable_sql: "SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
          safety,
          recommendations: ["Agent profile は最新です。"],
          repaired_sql: "",
          optimization_hints: [],
          timing,
        },
        {
          sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
          is_safe: true,
          row_limit: 100,
          note: "select ai",
          engine: "select_ai",
          engine_meta: { profile: "mock_select_ai" },
          fallback_reason: "",
          rewritten_question: "今月の請求金額が大きい取引先",
          executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
          safety,
          recommendations: ["Select AI profile は利用可能です。"],
          repaired_sql: "",
          optimization_hints: [],
          timing: { ...timing, elapsed_ms: 260 },
        },
      ],
      execution_results: [
        {
          engine: "select_ai_agent",
          executed: true,
          row_count: 1,
          error_message: "",
          elapsed_ms: 18,
          results: {
            columns: ["CUSTOMER_NAME", "TOTAL_AMOUNT"],
            rows: [{ CUSTOMER_NAME: "青山商事", TOTAL_AMOUNT: 1200000 }],
            total: 1,
          },
        },
        {
          engine: "select_ai",
          executed: false,
          row_count: 0,
          error_message: "ORA-00942 mock",
          elapsed_ms: 20,
          results: null,
        },
      ],
      error_rate: 0.5,
    })
  );

  return state;
}

async function expectNoHorizontalScroll(page: Page) {
  const size = await page.evaluate(() => ({
    width: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(size.scrollWidth).toBeLessThanOrEqual(size.width + 1);
}

test("query workbench previews SQL and executes the preview result", async ({ page }) => {
  const api = await mockNl2SqlApi(page);

  await page.goto("/query");
  await expect(page.getByText("スキーマ参照")).toBeVisible();

  const question = page.getByLabel("検索クエリ");
  await page.getByRole("button", { name: /^請求金額 TOTAL_AMOUNT/ }).click();
  await expect(question).toHaveValue("\"請求\".\"請求金額\"");

  await question.fill("請求金額を一覧で見たい");
  await expect(page.getByText("候補プロファイル")).toBeVisible();
  await expect(page.getByText("スコア 94%")).toBeVisible();
  await page.getByRole("button", { name: "候補を適用" }).click();

  await page.getByLabel(/請求金額 を選択/).check();
  await page.getByRole("button", { name: "SQL プレビュー" }).click();

  await expect(page.getByText("生成された SQL")).toBeVisible();
  await expect(page.getByText("SELECT CUSTOMER_NAME, TOTAL_AMOUNT FROM INVOICES")).toBeVisible();
  await page.getByRole("button", { name: "この SQL を実行" }).click();

  await expect(page.getByText("検索結果（1件）")).toBeVisible();
  await expect(page.getByRole("cell", { name: "青山商事" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "1200000" })).toBeVisible();
  expect(api.previewPayload?.allowed_objects).toEqual({
    table_names: ["INVOICES"],
    columns: { INVOICES: ["TOTAL_AMOUNT"] },
  });
  expect(api.executePayload?.sql).toContain("FETCH FIRST 100 ROWS ONLY");
  await expectNoHorizontalScroll(page);
});

test("evaluation page renders Select AI and Agent comparison details", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "エンジン比較" }).click();

  await expect(page.getByRole("heading", { name: "Select AI / Agent 比較" })).toBeVisible();
  await expect(page.getByText("比較エンジン", { exact: true })).toBeVisible();
  await expect(page.getByText("安全生成", { exact: true })).toBeVisible();
  await expect(page.getByText("最短", { exact: true })).toBeVisible();
  await expect(page.getByText("実行エラー率", { exact: true })).toBeVisible();
  await expect(page.getByText("50%", { exact: true })).toBeVisible();
  await expect(page.getByText("生成 SQL", { exact: true })).toHaveCount(2);
  await expect(page.getByText("参照表", { exact: true })).toHaveCount(2);
  await expect(page.getByText("実行済み", { exact: true })).toBeVisible();
  await expect(page.getByText("未実行", { exact: true })).toBeVisible();
  await expect(page.getByText("実行結果", { exact: true })).toBeVisible();
  await expect(page.getByText("ORA-00942 mock")).toBeVisible();
  await expect(page.getByText("Agent profile は最新です。")).toBeVisible();
  await expectNoHorizontalScroll(page);
});

test("history rerun deep-links back to query with question, engine, and profile", async ({ page }) => {
  await mockNl2SqlApi(page);

  await page.goto("/history");
  await expect(page.getByText("履歴から再実行したい請求金額")).toBeVisible();
  await page.getByRole("button", { name: "この質問で再実行" }).click();

  await expect(page).toHaveURL(/\/query\?/);
  await expect(page.getByLabel("検索クエリ")).toHaveValue("履歴から再実行したい請求金額");
  await expect(page.getByRole("button", { name: /Select AI Agent/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("業務プロファイル")).toHaveValue("default");
  await expectNoHorizontalScroll(page);
});
