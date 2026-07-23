import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

const basePath = "/api/nl2sql/quality-evaluations";

test.beforeEach(async ({ page }) => {
  await mockDatabaseGateReady(page);
});

function envelope(route: Route, data: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const capabilities = {
  engines: [
    { engine: "select_ai", label: "Select AI", available: true, reason: "" },
    {
      engine: "select_ai_agent",
      label: "Select AI Agent",
      available: false,
      reason: "Agent team が未構成です。",
    },
    {
      engine: "enterprise_ai_direct",
      label: "Enterprise AI Direct",
      available: true,
      reason: "",
    },
  ],
  judge: { available: true, reason: "", provider: "OCI Enterprise AI" },
  limits: {
    max_file_bytes: 10 * 1024 * 1024,
    max_cases: 100,
    max_attempts: 1000,
    min_repeat_count: 1,
    max_repeat_count: 10,
  },
};

const summary = [
  {
    engine: "select_ai",
    total_attempts: 2,
    generation_successes: 2,
    generation_success_rate: 1,
    correct: 1,
    incorrect: 1,
    uncertain: 0,
    not_analyzed: 0,
    normalized_sql_consistency: 0.5,
    error_count: 0,
  },
  {
    engine: "enterprise_ai_direct",
    total_attempts: 2,
    generation_successes: 1,
    generation_success_rate: 0.5,
    correct: 1,
    incorrect: 0,
    uncertain: 0,
    not_analyzed: 1,
    normalized_sql_consistency: 1,
    error_count: 1,
  },
];

function job(status: "pending" | "running" | "completed_with_errors" | "failed") {
  const terminal = status === "completed_with_errors" || status === "failed";
  return {
    job_id: "job-001",
    profile_id: "default",
    profile_name: "標準プロファイル",
    engines: ["select_ai", "enterprise_ai_direct"],
    repeat_count: 2,
    case_count: 1,
    total_attempts: 4,
    completed_attempts: terminal ? 4 : status === "running" ? 1 : 0,
    success_count: terminal ? 3 : status === "running" ? 1 : 0,
    error_count: terminal ? 1 : 0,
    status,
    current_case_id: status === "running" ? "CASE-001" : "",
    current_engine: status === "running" ? "select_ai" : null,
    current_repetition: status === "running" ? 2 : 0,
    engine_summaries: terminal && status !== "failed" ? summary : [],
    error_message: status === "failed" ? "worker の初期化に失敗しました。" : "",
    created_at: "2026-07-22T08:00:00Z",
    started_at: "2026-07-22T08:00:01Z",
    finished_at: terminal ? "2026-07-22T08:00:04Z" : null,
    updated_at: "2026-07-22T08:00:04Z",
  };
}

const results = [
  {
    result_id: "result-1",
    job_id: "job-001",
    case_no: 1,
    case_id: "CASE-001",
    excel_row: 2,
    question: "未入金の請求金額を取得してください",
    expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES WHERE STATUS = 'UNPAID'",
    engine: "select_ai",
    repetition_no: 1,
    generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES WHERE STATUS = 'UNPAID'",
    normalized_sql: "SELECT TOTAL_AMOUNT FROM INVOICES WHERE STATUS = 'UNPAID'",
    deterministic_analysis: {
      is_safe: true,
      is_select_only: true,
      referenced_objects: ["APP.INVOICES"],
      structure_summary: "INVOICES を STATUS で絞り込み",
      risk_findings: [],
    },
    generation_elapsed_ms: 120,
    judge_elapsed_ms: 240,
    total_elapsed_ms: 360,
    verdict: "correct",
    judge: {
      verdict: "correct",
      confidence: 0.96,
      summary: "質問と期待 SQL の意味に一致します。",
      differences: [],
      risks: [],
      correction_suggestion: "",
    },
    generation_error: "",
    judge_error: "",
    created_at: "2026-07-22T08:00:02Z",
  },
  {
    result_id: "result-2",
    job_id: "job-001",
    case_no: 1,
    case_id: "CASE-001",
    excel_row: 2,
    question: "未入金の請求金額を取得してください",
    expected_sql: "SELECT TOTAL_AMOUNT FROM INVOICES WHERE STATUS = 'UNPAID'",
    engine: "enterprise_ai_direct",
    repetition_no: 2,
    generated_sql: "",
    normalized_sql: "",
    deterministic_analysis: {
      is_safe: false,
      is_select_only: false,
      referenced_objects: [],
      structure_summary: "",
      risk_findings: [],
    },
    generation_elapsed_ms: 500,
    judge_elapsed_ms: 0,
    total_elapsed_ms: 500,
    verdict: "not_analyzed",
    judge: null,
    generation_error: "OCI Enterprise AI timeout",
    judge_error: "",
    created_at: "2026-07-22T08:00:03Z",
  },
];

async function mockQualityApi(
  page: Page,
  options: { judgeAvailable?: boolean; fixedStatus?: "completed_with_errors" | "failed" } = {}
) {
  let jobReads = 0;
  let submittedBody = "";
  await page.route("**/api/nl2sql/profiles**", (route) =>
    envelope(route, [
      {
        id: "default",
        name: "標準プロファイル",
        description: "",
        allowed_tables: [],
        allowed_views: [],
        glossary: {},
        sql_rules: [],
        default_row_limit: 100,
        safety_policy: "select_only",
        few_shot_examples: [],
        select_ai_config: {
          profile_name: "",
          region: "",
          model: "",
          embedding_model: "",
          max_tokens: 32000,
          enforce_object_list: true,
          comments: true,
          annotations: false,
          constraints: false,
          role: "",
          additional_instructions: "",
        },
        archived: false,
      },
    ])
  );
  await page.route("**/api/nl2sql/quality-evaluations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/capabilities")) {
      return envelope(route, {
        ...capabilities,
        judge:
          options.judgeAvailable === false
            ? {
                available: false,
                reason: "OCI Enterprise AI Judge が未構成です。",
                provider: "OCI Enterprise AI",
              }
            : capabilities.judge,
      });
    }
    if (path.endsWith("/template.xlsx")) {
      return route.fulfill({
        status: 200,
        contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers: {
          "Content-Disposition": 'attachment; filename="nl2sql_quality_evaluation_template.xlsx"',
        },
        body: Buffer.from("template"),
      });
    }
    if (path.endsWith("/results.xlsx")) {
      return route.fulfill({
        status: 200,
        contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers: {
          "Content-Disposition":
            'attachment; filename="nl2sql_quality_evaluation_20260722_job-001.xlsx"',
        },
        body: Buffer.from("result workbook"),
      });
    }
    if (path.endsWith("/results")) {
      return envelope(route, { items: results, next_cursor: null, total: results.length });
    }
    if (path === basePath && request.method() === "POST") {
      submittedBody = request.postData() ?? "";
      return envelope(route, job("pending"), 202);
    }
    if (path === basePath) {
      const recent = options.fixedStatus ? job(options.fixedStatus) : job("completed_with_errors");
      return envelope(route, { items: [recent], next_cursor: null, total: 1 });
    }
    if (path.endsWith("/job-001")) {
      if (options.fixedStatus) return envelope(route, job(options.fixedStatus));
      jobReads += 1;
      return envelope(route, job(jobReads < 2 ? "running" : "completed_with_errors"));
    }
    return route.fallback();
  });
  return { submittedBody: () => submittedBody };
}

test("desktop executes two engines twice, restores the job URL and downloads Excel", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop flow");
  const state = await mockQualityApi(page);
  await page.goto("/evaluation");

  await expect(page.getByRole("heading", { name: "評価条件" })).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  const checkboxes = page.getByRole("checkbox");
  await checkboxes.nth(0).focus();
  await page.keyboard.press("Space");
  await checkboxes.nth(2).check();
  await expect(checkboxes.nth(1)).toBeDisabled();
  await page.getByLabel("繰り返し回数").fill("2");
  await page.locator('input[type="file"]').setInputFiles({
    name: "quality-cases.xlsx",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("mock xlsx"),
  });
  await page.getByRole("button", { name: "評価を開始" }).click();

  await expect(page).toHaveURL(/\?job=job-001/);
  await expect(page.getByText("CASE-001").first()).toBeVisible();
  await expect(page.getByText("一部エラーで完了").first()).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole("heading", { name: "評価概要" })).toBeVisible();
  await expect(
    page.getByText("質問と期待 SQL の意味に一致します。").filter({ visible: true }).first()
  ).toBeVisible();
  expect(state.submittedBody()).toContain("select_ai");
  expect(state.submittedBody()).toContain("enterprise_ai_direct");
  expect(state.submittedBody()).toContain("repeat_count");

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "結果 Excel をダウンロード" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(
    "nl2sql_quality_evaluation_20260722_job-001.xlsx"
  );
});

test("mobile restores completed results as cards without page overflow", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-375", "mobile flow");
  await mockQualityApi(page, { fixedStatus: "completed_with_errors" });
  await page.goto("/evaluation?job=job-001");

  await expect(
    page
      .locator("article")
      .filter({ hasText: "未入金の請求金額を取得してください", visible: true })
      .first()
  ).toBeVisible();
  await expect(page.locator("table")).toBeHidden();
  await expect(
    page.getByText("OCI Enterprise AI timeout").filter({ visible: true }).first()
  ).toBeVisible();
  const noHorizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth
  );
  expect(noHorizontalOverflow).toBe(true);
});

test("form errors, unavailable engines, Judge readiness and failed jobs are explicit", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop validation states");
  await mockQualityApi(page);
  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価を開始" }).click();
  await expect(page.getByText(".xlsx ファイルを選択してください。")).toBeVisible();
  await expect(page.getByText("利用可能な実行エンジンを1つ以上選択してください。")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles({
    name: "invalid.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("QUESTION,EXPECTED_SQL"),
  });
  await page.getByRole("checkbox").nth(0).check();
  await page.getByRole("button", { name: "評価を開始" }).click();
  await expect(page.getByText(".xlsx ファイルのみ使用できます。")).toBeVisible();

  await page.unrouteAll({ behavior: "wait" });
  await mockDatabaseGateReady(page);
  await mockQualityApi(page, { judgeAvailable: false, fixedStatus: "failed" });
  await page.goto("/evaluation?job=job-001");
  await expect(page.getByText("LLM Judge を利用できません", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "評価を開始" })).toBeDisabled();
  await expect(
    page.getByText("worker の初期化に失敗しました。").filter({ visible: true }).first()
  ).toBeVisible();
});
