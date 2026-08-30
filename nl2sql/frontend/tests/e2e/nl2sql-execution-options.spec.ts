import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

const safety = {
  is_safe: true,
  is_select_only: true,
  row_limit_applied: 100,
  blocked_reason: "",
  warnings: [],
  referenced_tables: ["APP.INVOICES"],
  referenced_columns: ["APP.INVOICES.TOTAL_AMOUNT"],
};

const timing = {
  created_at: "2026-08-16T00:00:00.000Z",
  started_at: "2026-08-16T00:00:00.010Z",
  finished_at: "2026-08-16T00:00:00.160Z",
  elapsed_ms: 150,
  stage_timings: [
    { stage: "prepare_context", elapsed_ms: 10 },
    { stage: "generate_sql", elapsed_ms: 40 },
    { stage: "safety_check", elapsed_ms: 20 },
    { stage: "execute_sql", elapsed_ms: 50 },
    { stage: "format_results", elapsed_ms: 30 },
  ],
};

const profile = {
  id: "default",
  name: "既定プロファイル",
  category: "既定",
  description: "請求を扱うプロファイル",
  allowed_tables: ["APP.INVOICES"],
  allowed_views: [],
  glossary: { 請求金額: "INVOICES.TOTAL_AMOUNT" },
  sql_rules: ["SELECT のみ"],
  default_row_limit: 100,
  safety_policy: "select_only",
  few_shot_examples: [],
  select_ai_config: {
    profile_name: "NL2SQL_DEFAULT_PROFILE",
    region: "ap-osaka-1",
    model: "cohere.command-r-plus",
    embedding_model: "cohere.embed-v4.0",
    max_tokens: 32000,
    enforce_object_list: true,
    comments: true,
    annotations: false,
    constraints: false,
    role: "",
    additional_instructions: "",
  },
  archived: false,
  version: 1,
  etag: "profile-etag",
  updated_at: "2026-08-16T00:00:00.000Z",
};

const schemaTable = {
  table_name: "INVOICES",
  qualified_name: "APP.INVOICES",
  logical_name: "請求情報を管理するテーブル",
  owner: "APP",
  table_type: "TABLE",
  comment: "請求情報",
  row_count: 2,
  columns: [
    {
      column_name: "TOTAL_AMOUNT",
      logical_name: "請求金額",
      data_type: "NUMBER",
      nullable: false,
      comment: "税込請求金額",
      sample_values: ["1200000"],
    },
  ],
  constraints: [],
};

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

async function mockNl2SqlWorkbenchApi(page: Page) {
  const state: {
    jobPayload: Record<string, unknown> | null;
    rewritePayload: Record<string, unknown> | null;
  } = { jobPayload: null, rewritePayload: null };
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          id: profile.id,
          name: profile.name,
          category: profile.category,
          description: profile.description,
          archived: false,
          allowed_table_count: 1,
          allowed_view_count: 0,
          glossary_count: 1,
          few_shot_count: 0,
          version: 1,
          etag: profile.etag,
          updated_at: profile.updated_at,
        },
      ],
      next_cursor: null,
      total: 1,
      change_token: 1,
    })
  );
  await page.route("**/api/nl2sql/profiles/default", (route) => fulfillJson(route, profile));
  await page.route("**/api/nl2sql/profiles/default/usage-context", (route) =>
    fulfillJson(route, {
      id: profile.id,
      name: profile.name,
      category: profile.category,
      description: profile.description,
      allowed_tables: profile.allowed_tables,
      allowed_views: profile.allowed_views,
      archived: profile.archived,
      object_scope_version: 1,
      version: profile.version,
      etag: profile.etag,
      updated_at: profile.updated_at,
    })
  );
  await page.route("**/api/schema/catalog/head", (route) =>
    fulfillJson(route, {
      catalog_version: 1,
      schema_fingerprint: "schema-fixture",
      refreshed_at: "2026-08-16T00:00:00.000Z",
      object_count: 1,
      column_count: 1,
      change_token: 1,
      etag: "schema-etag",
    })
  );
  await page.route("**/api/schema/objects?*", (route) =>
    fulfillJson(route, {
      items: [
        {
          owner: "APP",
          object_name: "INVOICES",
          object_type: "TABLE",
          logical_name: schemaTable.logical_name,
          comment: schemaTable.comment,
          row_count: schemaTable.row_count,
          column_count: schemaTable.columns.length,
          last_ddl_at: "",
        },
      ],
      next_cursor: null,
      total: 1,
      catalog_version: 1,
    })
  );
  await page.route("**/api/schema/objects/APP/INVOICES", (route) =>
    fulfillJson(route, {
      table: schemaTable,
      dependencies: [],
      catalog_version: 1,
      etag: "schema-etag",
    })
  );
  await page.route("**/api/nl2sql/history", (route) =>
    fulfillJson(route, { items: [], total: 0 })
  );
  await page.route("**/api/nl2sql/recommend-profile", (route) =>
    fulfillJson(route, {
      recommended_profile_id: "default",
      recommended_profile_name: "既定プロファイル",
      confidence: 0.2,
      recommendation_source: "deterministic",
      reasons: [],
      recommended_allowed_objects: { table_names: ["APP.INVOICES"], columns: {} },
    })
  );
  await page.route("**/api/nl2sql/similar-history", (route) =>
    fulfillJson(route, { items: [] })
  );
  await page.route("**/api/nl2sql/rewrite", (route) => {
    state.rewritePayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      original_question: "請求金額を確認したい",
      rewritten_question: "書き換え後の請求金額",
      source: "deterministic",
      model: "",
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/jobs", (route) => {
    state.jobPayload = route.request().postDataJSON() as Record<string, unknown>;
    return fulfillJson(route, {
      job_id: "job-options-001",
      status: "pending",
      created_at: timing.created_at,
      steps: [],
    });
  });
  await page.route("**/api/nl2sql/jobs/job-options-001", (route) =>
    fulfillJson(route, {
      job_id: "job-options-001",
      status: "done",
      created_at: timing.created_at,
      started_at: timing.started_at,
      finished_at: timing.finished_at,
      elapsed_ms: timing.elapsed_ms,
      error_message: null,
      timing,
      steps: timing.stage_timings.map((item) => ({
        stage: item.stage,
        status: "done",
        elapsed_ms: item.elapsed_ms,
      })),
      result: {
        engine: "select_ai",
        engine_meta: { runtime: "oracle", select_ai_profile: "NL2SQL_DEFAULT_PROFILE" },
        fallback_reason: "",
        original_question: "書き換え後の請求金額",
        rewritten_question: "書き換え後の請求金額",
        generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
        executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES FETCH FIRST 100 ROWS ONLY",
        explanation: "SQL を生成しました。",
        safety,
        recommendations: [],
        repaired_sql: "",
        optimization_hints: [],
        results: {
          columns: ["TOTAL_AMOUNT"],
          rows: [{ TOTAL_AMOUNT: 1200000 }],
          total: 1,
        },
        timing,
        interpretation: {
          available: true,
          question: {
            available: true,
            source: "deterministic",
            original_question: "書き換え後の請求金額",
            rewritten_question: "書き換え後の請求金額",
            profile_id: "default",
            profile_name: "既定プロファイル",
            target_objects: ["APP.INVOICES"],
            filters: [],
            group_by: [],
            order_by: [],
            aggregations: ["SUM"],
            row_limit: 100,
            confidence: 0.9,
            warnings: [],
          },
          sql: {
            available: true,
            source: "sql_semantics",
            summary: "APP.INVOICES を参照し、SELECT 操作を行います。",
            statement_type: "SELECT",
            tables: ["APP.INVOICES"],
            columns: ["APP.INVOICES.TOTAL_AMOUNT"],
            joins: [],
            filters: [],
            aggregations: ["SUM"],
            group_by: [],
            order_by: [],
            limit: 100,
            semantic_graph: {},
            warnings: [],
          },
          warnings: [],
        },
        show_prompt: {
          available: true,
          engine: "select_ai",
          action: "showprompt",
          prompt: "Select AI prompt body\nUse APP.INVOICES only.",
          unavailable_reason: "",
          warnings: [],
        },
      },
    })
  );
  return state;
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(() =>
      page.evaluate(() => {
        const element = document.scrollingElement ?? document.documentElement;
        return element.scrollWidth - element.clientWidth;
      })
    )
    .toBeLessThanOrEqual(2);
}

test("unified execute button runs SQL and renders execution artifacts", async ({ page }) => {
  const api = await mockNl2SqlWorkbenchApi(page);
  await page.goto("/query");

  await expect(page.getByRole("button", { name: "SQL プレビュー" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "質問を解釈" })).toHaveCount(0);
  await expect(page.getByText("履歴 0 件")).toHaveCount(0);
  const executionOptionsDisclosure = page.getByRole("button", { name: /実行オプション/ });
  const executionOptionsChevron = executionOptionsDisclosure.locator('svg[data-state]');
  await expect(executionOptionsDisclosure).toHaveAttribute("aria-expanded", "false");
  await expect(executionOptionsChevron).toHaveAttribute("data-state", "collapsed");
  await expect(executionOptionsChevron).toHaveClass(/rotate-90/);
  await expect(page.getByLabel("Ontology を使う")).toBeHidden();
  await executionOptionsDisclosure.focus();
  await page.keyboard.press("Enter");
  await expect(executionOptionsDisclosure).toHaveAttribute("aria-expanded", "true");
  await expect(executionOptionsChevron).toHaveAttribute("data-state", "expanded");
  await expect(executionOptionsChevron).toHaveClass(/rotate-0/);
  const glossaryOption = page.getByLabel("用語・同義語を使う");
  await expect(glossaryOption).toBeChecked();
  await expect(page.getByLabel("Schema を使う")).toHaveCount(0);
  await expect(page.getByLabel("Ontology を使う")).toBeChecked();
  await expect(page.getByLabel("解釈を表示")).toBeChecked();
  await expect(page.getByLabel("Show Prompt を表示")).toBeChecked();
  await expect(executionOptionsDisclosure).not.toContainText("条件あり");
  await glossaryOption.uncheck();
  await expect(executionOptionsDisclosure).toContainText("条件あり");
  await glossaryOption.check();
  await expect(executionOptionsDisclosure).not.toContainText("条件あり");
  await expect(page.getByTestId("nl2sql-execution-options")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.locator("#nl2sql-question-input").fill("請求金額を確認したい");
  await page.getByRole("button", { name: "検索を実行" }).click();

  await expect.poll(() => api.rewritePayload).not.toBeNull();
  expect(api.rewritePayload).toEqual({
    question: "請求金額を確認したい",
    profile_id: "default",
    use_glossary: true,
    extra_prompt: "",
  });
  await expect.poll(() => api.jobPayload).not.toBeNull();
  expect(api.jobPayload).toMatchObject({
    question: "書き換え後の請求金額",
    use_ontology_context: true,
    include_interpretation: true,
    include_show_prompt: true,
  });
  await expect(page.getByRole("textbox", { name: "生成 SQL" })).toHaveValue(
    /SELECT TOTAL_AMOUNT FROM INVOICES/
  );
  await expect(page.getByTestId("nl2sql-interpretation-panel")).toHaveCount(0);
  await expect(page.getByText("入力と生成 SQL の対応")).toHaveCount(0);
  await expect(page.getByText("入力テンプレート")).toHaveCount(0);
  await expect(page.getByText("生成 SQL の意味")).toHaveCount(0);
  await expect(page.getByText("APP.INVOICES を参照し、SELECT 操作を行います。")).toHaveCount(0);
  await expect(page.getByText("1200000")).toBeVisible();

  const showPromptPanel = page.getByTestId("nl2sql-show-prompt-panel");
  const showPromptSummary = showPromptPanel.locator("summary");
  const showPromptChevron = showPromptPanel.getByTestId("nl2sql-show-prompt-chevron");
  const showPromptBody = showPromptPanel.getByText("Select AI prompt body");
  await expect(showPromptPanel).toBeVisible();
  await expect(showPromptBody).toBeHidden();
  await expect(showPromptChevron).toHaveAttribute("data-state", "collapsed");
  await expect(showPromptChevron).toHaveClass(/rotate-90/);

  await showPromptSummary.click();
  await expect(showPromptBody).toBeVisible();
  await expect(showPromptChevron).toHaveAttribute("data-state", "expanded");
  await expect(showPromptChevron).toHaveClass(/rotate-0/);

  await showPromptSummary.click();
  await expect(showPromptBody).toBeHidden();
  await expect(showPromptChevron).toHaveAttribute("data-state", "collapsed");
  await expect(showPromptChevron).toHaveClass(/rotate-90/);

  await showPromptSummary.focus();
  await page.keyboard.press("Enter");
  await expect(showPromptBody).toBeVisible();
  await expect(showPromptChevron).toHaveAttribute("data-state", "expanded");
  await expect(showPromptChevron).toHaveClass(/rotate-0/);

  await showPromptSummary.focus();
  await page.keyboard.press("Space");
  await expect(showPromptBody).toBeHidden();
  await expect(showPromptChevron).toHaveAttribute("data-state", "collapsed");
  await expect(showPromptChevron).toHaveClass(/rotate-90/);

  const ontologyOption = page.getByLabel("Ontology を使う");
  await ontologyOption.focus();
  await expect(ontologyOption).toBeFocused();
  await page.keyboard.press("Space");
  await expect(ontologyOption).not.toBeChecked();
  api.jobPayload = null;
  await page.getByRole("button", { name: "検索を実行" }).click();
  await expect.poll(() => api.jobPayload).not.toBeNull();
  expect(api.jobPayload).toMatchObject({
    use_ontology_context: false,
  });
  await expect(executionOptionsDisclosure).toContainText("条件あり");
  const resetButton = page.getByRole("button", { name: "リセット" });
  await expect(resetButton).toBeEnabled();
  await resetButton.click();
  await expect(executionOptionsDisclosure).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByLabel("Ontology を使う")).toBeHidden();
  await executionOptionsDisclosure.click();
  await expect(page.getByLabel("Ontology を使う")).toBeChecked();
  await expect(page.getByLabel("用語・同義語を使う")).toBeChecked();
  await expect(page.getByLabel("Schema を使う")).toHaveCount(0);
  await expect(page.getByLabel("解釈を表示")).toBeChecked();
  await expect(page.getByLabel("Show Prompt を表示")).toBeChecked();
  await expectNoHorizontalOverflow(page);
});

test("execution options keep ontology toggle usable at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 900 });
  await mockNl2SqlWorkbenchApi(page);
  await page.goto("/query");

  const options = page.getByTestId("nl2sql-execution-options");
  const executionOptionsDisclosure = page.getByRole("button", { name: /実行オプション/ });
  const ontologyOption = page.getByLabel("Ontology を使う");
  await expect(options).toBeVisible();
  await expect(executionOptionsDisclosure).toHaveAttribute("aria-expanded", "false");
  await expect(ontologyOption).toBeHidden();
  await executionOptionsDisclosure.focus();
  await page.keyboard.press("Space");
  await expect(executionOptionsDisclosure).toHaveAttribute("aria-expanded", "true");
  await expect(ontologyOption).toBeVisible();
  await expect(ontologyOption).toBeChecked();
  await ontologyOption.focus();
  await page.keyboard.press("Space");
  await expect(ontologyOption).not.toBeChecked();
  await expectNoHorizontalOverflow(page);
});
