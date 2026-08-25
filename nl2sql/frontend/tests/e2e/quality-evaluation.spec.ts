import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { dropFiles } from "./_helpers/file-dropzone";

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

async function visibleBox(locator: Locator) {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  return box!;
}

async function expectVerticalOrder(locators: Locator[]) {
  if (locators.length === 0) return;
  let previous = await visibleBox(locators[0]!);
  for (const locator of locators.slice(1)) {
    const current = await visibleBox(locator);
    expect(current.y).toBeGreaterThanOrEqual(previous.y + previous.height - 1);
    previous = current;
  }
}

const capabilities = {
  engines: [
    { engine: "select_ai", label: "Select AI", available: true, reason: "" },
    {
      engine: "select_ai_agent",
      label: "Select AI Agent",
      available: true,
      reason: "",
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

const longEvaluationQuestion =
  '対象テーブル："部署情報を管理するテーブル" 抽出項目："DEPARTMENT_ID", "DEPARTMENT_NAME", "LOCATION", "CREATED_AT" 抽出条件：未入金の請求金額を確認し、VERY_LONG_UNBROKEN_EVALUATION_QUERY_IDENTIFIER_0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ を含めて取得してください';

function job(status: "pending" | "running" | "completed_with_errors" | "failed") {
  const terminal = status === "completed_with_errors" || status === "failed";
  return {
    job_id: "job-001",
    profile_id: "default",
    profile_name: "標準プロファイル",
    profile_category: "品質評価",
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
    question: longEvaluationQuestion,
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
    question: longEvaluationQuestion,
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
  options: {
    judgeAvailable?: boolean;
    fixedStatus?: "completed_with_errors" | "failed";
    engines?: typeof capabilities.engines;
  } = {}
) {
  let jobReads = 0;
  let submittedBody = "";
  await page.route("**/api/nl2sql/profiles**", (route) =>
    envelope(route, {
      items: [
        {
          id: "default",
          name: "標準プロファイル",
          category: "品質評価",
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
      ],
      next_cursor: null,
      total: 1,
    })
  );
  await page.route("**/api/nl2sql/quality-evaluations**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path.endsWith("/capabilities")) {
      return envelope(route, {
        ...capabilities,
        engines: options.engines ?? capabilities.engines,
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
  const inputRow = page.getByTestId("quality-evaluation-input-row");
  const profileField = page.getByTestId("quality-evaluation-profile-field");
  const fileField = page.getByTestId("quality-evaluation-file");
  const engineFieldset = page.getByTestId("quality-evaluation-engine-fieldset");
  const repeatLabel = page.getByTestId("quality-evaluation-repeat-label");
  const repeatInput = page.getByLabel("繰り返し回数");
  const estimateSummary = page.getByTestId("quality-evaluation-estimate-summary");
  const estimateLabel = page.getByTestId("quality-evaluation-estimate-label");
  const estimateValue = page.getByTestId("quality-evaluation-estimate-value");
  const actionFooter = page.getByTestId("quality-evaluation-action-footer");
  const [
    inputRowBox,
    profileBox,
    fileBox,
    engineBox,
    repeatLabelBox,
    repeatInputBox,
    estimateBox,
    estimateLabelBox,
    estimateValueBox,
    footerBox,
  ] =
    await Promise.all([
      visibleBox(inputRow),
      visibleBox(profileField),
      visibleBox(fileField),
      visibleBox(engineFieldset),
      visibleBox(repeatLabel),
      visibleBox(repeatInput),
      visibleBox(estimateSummary),
      visibleBox(estimateLabel),
      visibleBox(estimateValue),
      visibleBox(actionFooter),
    ]);
  expect(Math.abs(profileBox.y - fileBox.y)).toBeLessThanOrEqual(3);
  expect(engineBox.y).toBeGreaterThanOrEqual(inputRowBox.y + inputRowBox.height - 1);
  expect(Math.abs(repeatLabelBox.y - estimateLabelBox.y)).toBeLessThanOrEqual(3);
  expect(Math.abs(repeatInputBox.y - estimateValueBox.y)).toBeLessThanOrEqual(3);
  expect(estimateValueBox.height).toBeLessThanOrEqual(repeatInputBox.height + 8);
  expect(estimateBox.height).toBeLessThanOrEqual(repeatInputBox.height + repeatLabelBox.height + 16);
  const estimateSurface = await estimateSummary.evaluate((element) => {
    const styles = window.getComputedStyle(element);
    return {
      backgroundColor: styles.backgroundColor,
      borderTopWidth: styles.borderTopWidth,
      borderRightWidth: styles.borderRightWidth,
      borderBottomWidth: styles.borderBottomWidth,
      borderLeftWidth: styles.borderLeftWidth,
    };
  });
  expect(estimateSurface.backgroundColor).toBe("rgba(0, 0, 0, 0)");
  expect(estimateSurface.borderTopWidth).toBe("0px");
  expect(estimateSurface.borderRightWidth).toBe("0px");
  expect(estimateSurface.borderBottomWidth).toBe("0px");
  expect(estimateSurface.borderLeftWidth).toBe("0px");
  expect(footerBox.y).toBeGreaterThanOrEqual(estimateBox.y + estimateBox.height - 1);
  const profileSelect = page.locator("#quality-evaluation-profile");
  await expect(profileSelect).toHaveValue("default");
  await expect(
    profileSelect.locator("option", { hasText: "標準プロファイル（品質評価）" })
  ).toHaveCount(1);
  await expect(
    page.getByText("標準プロファイル（品質評価）").filter({ visible: true }).first()
  ).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  const checkboxes = page.getByRole("checkbox");
  const engineBulkActions = page.getByTestId("quality-evaluation-engine-selection-actions");
  await expect(engineBulkActions.getByRole("button", { name: "すべて選択" })).toBeEnabled();
  await expect(engineBulkActions.getByRole("button", { name: "すべて解除" })).toBeDisabled();
  await engineBulkActions.getByRole("button", { name: "すべて選択" }).click();
  await expect(checkboxes.nth(0)).toBeChecked();
  await expect(checkboxes.nth(1)).toBeEnabled();
  await expect(checkboxes.nth(1)).toBeChecked();
  await expect(checkboxes.nth(2)).toBeChecked();
  await expect(engineBulkActions.getByRole("button", { name: "すべて解除" })).toBeEnabled();
  await engineBulkActions.getByRole("button", { name: "すべて解除" }).click();
  await expect(checkboxes.nth(0)).not.toBeChecked();
  await expect(checkboxes.nth(1)).not.toBeChecked();
  await expect(checkboxes.nth(2)).not.toBeChecked();
  await checkboxes.nth(0).focus();
  await page.keyboard.press("Space");
  await checkboxes.nth(2).check();
  await expect(checkboxes.nth(1)).toBeEnabled();
  await page.getByLabel("繰り返し回数").fill("2");
  await dropFiles(page, page.getByTestId("quality-evaluation-file-dropzone"), [
    {
      name: "quality-cases.xlsx",
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      content: "mock xlsx",
    },
  ]);
  await page.getByRole("button", { name: "評価を開始" }).click();

  await expect(page).toHaveURL(/\?job=job-001/);
  await expect(page.getByText("CASE-001").first()).toBeVisible();
  await expect(page.getByText("一部エラーで完了").first()).toBeVisible({ timeout: 8_000 });
  await expect(page.getByRole("heading", { name: "評価概要" })).toBeVisible();
  await expect(
    page.getByTestId("quality-evaluation-timing").getByRole("timer")
  ).toHaveAccessibleName("処理時間 00:03");
  await expect(page.getByRole("progressbar", { name: "実行状況" })).toHaveAttribute(
    "aria-valuemax",
    "4",
  );
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

test("unavailable engines remain disabled when capabilities mark them unavailable", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "desktop unavailable engine state");
  await mockQualityApi(page, {
    engines: capabilities.engines.map((engine) =>
      engine.engine === "select_ai_agent"
        ? { ...engine, available: false, reason: "Agent team が未構成です。" }
        : engine
    ),
  });
  await page.goto("/evaluation");

  const checkboxes = page.getByRole("checkbox");
  await expect(checkboxes.nth(0)).toBeEnabled();
  await expect(checkboxes.nth(1)).toBeDisabled();
  await expect(checkboxes.nth(2)).toBeEnabled();
  await expect(page.getByText("Agent team が未構成です。")).toBeVisible();
  await expect(page.getByText("利用不可")).toBeVisible();
});

test("mobile restores completed results as cards without page overflow", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name !== "mobile-375", "mobile flow");
  await mockQualityApi(page, { fixedStatus: "completed_with_errors" });
  await page.goto("/evaluation?job=job-001");

  await expectVerticalOrder([
    page.getByTestId("quality-evaluation-profile-field"),
    page.getByTestId("quality-evaluation-file"),
    page.getByTestId("quality-evaluation-engine-fieldset"),
    page.getByTestId("quality-evaluation-repeat-field"),
    page.getByTestId("quality-evaluation-estimate-summary"),
    page.getByTestId("quality-evaluation-judge-note"),
    page.getByTestId("quality-evaluation-action-footer"),
  ]);
  await expect(
    page
      .locator("article")
      .filter({ hasText: "対象テーブル", visible: true })
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
  await expect(page.getByTestId("quality-evaluation-file-input")).toHaveAttribute(
    "accept",
    ".xlsx"
  );
  await expect(page.getByText(".XLSX", { exact: true })).toBeVisible();
  await dropFiles(page, page.getByTestId("quality-evaluation-file-dropzone"), [
    {
      name: "invalid.csv",
      type: "text/csv",
      content: "QUESTION,EXPECTED_SQL",
    },
  ]);
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
