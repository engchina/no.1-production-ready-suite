import { expect, type Page, test } from "@playwright/test";

import { mockEvaluationJobs } from "./_evaluation-jobs";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockKnowledgeBases(page);
  await mockEvaluationSuiteSettings(page, "balanced");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`評価ページは品質評価の既定スイートを表示する (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });

    await page.goto("/evaluation");

    await expect(
      page.getByRole("heading", { name: "品質評価", level: 1 })
    ).toBeVisible();
    // 既定は「設定の既定に従う」で、現在のグローバル既定(バランス)を表示する。
    await expect(page.getByText("設定の既定に従う(現在: バランス)")).toBeVisible();
    // 既定スイート(バランス)の閾値プレビューが見える。
    await expect(page.getByText("Precision@K").first()).toBeVisible();
    await expect(
      page.getByRole("link", { name: "設定で既定スイートを変更" })
    ).toHaveAttribute("href", "/settings/evaluation");
    await expectNoPageOverflow(page);
  });
}

test("既定スイートのまま評価実行すると suite を送らず適用スイートを表示する", async ({
  page,
}) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "balanced"),
    autoComplete: true,
  });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();

  await expect.poll(() => jobs.runPayloads[0] && "suite" in jobs.runPayloads[0]).toBe(false);
  await expect(page.getByText("適用スイート: バランス")).toBeVisible();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`評価の job は進捗・今のケース・経過時間を出し、完了したら結果を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const jobs = await mockEvaluationJobs(page, {
      runResult: () => evaluationMetrics("balanced"),
    });

    await page.goto("/evaluation");
    await page.getByRole("button", { name: "評価実行" }).click();

    // 実行状況（#390）: 状態・件数・今のケース・経過時間（placement="job"）と取り消し。
    const panel = page.getByTestId("evaluation-run-job");
    await expect(panel.locator("[data-status-variant]")).toHaveText("実行中");
    await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 1 件（0%）");
    await expect(page.getByTestId("evaluation-run-job-current-case")).toHaveText(
      "実行中のケース: policy-approval-flow-basic"
    );
    const timing = page.getByTestId("evaluation-run-job-timing");
    await expect(timing).toHaveAttribute("data-processing-placement", "job");
    await expect(timing.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
    await expect(panel.getByRole("button", { name: "取り消し" })).toBeVisible();
    // 実行中は同じ評価を重ねて投入しない。
    await expect(page.getByRole("button", { name: "評価実行" })).toBeDisabled();
    await expect(page.getByTestId("evaluation-result-loading")).toBeVisible();
    await expectNoPageOverflow(page);

    jobs.complete("run");
    await expect(page.getByText("適用スイート: バランス")).toBeVisible();
    await expect(panel.locator("[data-status-variant]")).toHaveText("完了");
    await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("1 / 1 件（100%）");
    await expect(timing.getByRole("timer")).toHaveAccessibleName(/処理時間 \d{2}:\d{2}/);
    await expect(panel.getByRole("button", { name: "取り消し" })).toHaveCount(0);
    await expect(page.getByTestId("evaluation-result-loading")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "評価実行" })).toBeEnabled();
    await expectNoPageOverflow(page);
  });

  test(`失敗したケースは理由と時間切れになった工程を表に出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockEvaluationJobs(page, {
      runResult: () => evaluationMetricsWithTimedOutCase(),
      autoComplete: true,
    });

    await page.goto("/evaluation");
    await page.getByRole("button", { name: "評価実行" }).click();

    const error = page.getByTestId("evaluation-case-error");
    await expect(error).toContainText("case-slow");
    await expect(error).toContainText("エラー");
    await expect(error).toContainText("工程: 検索の計画");
    await expect(error).toContainText(
      "評価ケースの回答生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 検索の計画）。"
    );
    await expectNoPageOverflow(page);
  });
}

test("実行中の評価は確認してから取り消せる", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("balanced") });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();
  const panel = page.getByTestId("evaluation-run-job");
  await panel.getByRole("button", { name: "取り消し" }).click();

  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("評価を取り消しますか？");
  await dialog.getByRole("button", { name: "取り消す" }).click();

  await expect.poll(() => jobs.cancelled.length).toBe(1);
  await expect(panel.locator("[data-status-variant]")).toHaveText("取り消し済み");
  await expect(panel.getByText("評価を取り消しました。")).toBeVisible();
  await expect(page.getByTestId("evaluation-result-loading")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "評価実行" })).toBeEnabled();
});

test("再読込しても実行中の評価の job の状態を表示し、失敗の理由を出す", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("balanced") });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 1 件（0%）");

  // 戻っただけで評価を送り直さず、保存した job id でサーバーの状態を確かめる（workspace-state.md）。
  await page.reload();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 1 件（0%）");
  expect(jobs.runPayloads).toHaveLength(1);

  jobs.fail("run", "品質評価の実行に失敗しました（RuntimeError）。");
  const panel = page.getByTestId("evaluation-run-job");
  await expect(panel.locator("[data-status-variant]")).toHaveText("失敗");
  await expect(panel.getByText("評価を最後まで実行できませんでした。")).toBeVisible();
  await expect(panel.getByText("品質評価の実行に失敗しました（RuntimeError）。")).toBeVisible();
});

test("スイートを選ぶと閾値プレビューを更新し suite を送る", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "balanced"),
    autoComplete: true,
  });

  await page.goto("/evaluation");

  await page.getByRole("combobox", { name: "品質評価" }).click();
  await page
    .getByRole("listbox", { name: "品質評価" })
    .getByRole("option", { name: /厳格 CI/ })
    .click();

  // strict_ci の閾値(groundedness / citation traceability)がプレビューに出る。
  await expect(page.getByText("Groundedness").first()).toBeVisible();
  await expect(page.getByText("Citation Traceability").first()).toBeVisible();

  await page.getByRole("button", { name: "評価実行" }).click();

  await expect.poll(() => jobs.runPayloads[0]?.suite).toBe("strict_ci");
  await expect(page.getByText("適用スイート: 厳格 CI")).toBeVisible();
  await expectNoPageOverflow(page);
});

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: { items: [], total: 0, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockEvaluationSuiteSettings(page: Page, suite: string) {
  await page.route("**/api/settings/evaluation-suite", async (route) => {
    await route.fulfill({ json: evaluationSuiteEnvelope(suite) });
  });
}

function evaluationSuiteEnvelope(suite: string) {
  const specs: { name: string; thresholds: Record<string, number>; focus_metrics: string[] }[] = [
    { name: "request_only", thresholds: {}, focus_metrics: [] },
    {
      name: "retrieval_focused",
      thresholds: { precision_at_k: 0.6, recall_at_k: 0.8, mrr: 0.7 },
      focus_metrics: ["precision_at_k", "recall_at_k", "mrr"],
    },
    {
      name: "balanced",
      thresholds: { precision_at_k: 0.6, recall_at_k: 0.8, mrr: 0.7, groundedness_pass_rate: 0.9 },
      focus_metrics: ["precision_at_k", "groundedness_pass_rate"],
    },
    {
      name: "strict_ci",
      thresholds: { groundedness_pass_rate: 0.95, citation_traceability_coverage: 0.9 },
      focus_metrics: ["groundedness_pass_rate", "citation_traceability_coverage"],
    },
    {
      name: "ragas_like",
      thresholds: { faithfulness: 0.8, context_recall: 0.8 },
      focus_metrics: ["faithfulness", "context_recall"],
    },
  ];
  const selected = specs.find((item) => item.name === suite) ?? specs[0];
  return {
    data: {
      suite,
      thresholds: selected.thresholds,
      focus_metrics: selected.focus_metrics,
      suites: specs.map((item) => ({
        ...item,
        origin: "x",
        recommended_for: ["general"],
        selected: item.name === suite,
      })),
      config_source: "runtime",
    },
    error_messages: [],
    warning_messages: [],
  };
}

function evaluationMetrics(suite: string) {
  return {
    case_count: 1,
    error_count: 0,
    evaluation_suite: suite,
    evaluated_k: 10,
    precision_at_k: 1,
    recall_at_k: 1,
    mrr: 1,
    answer_keyword_hit_rate: 1,
    groundedness_pass_rate: 1,
    faithfulness: 1,
    context_precision: 1,
    context_recall: 1,
    response_relevancy: 1,
    noise_sensitivity: 1,
    citation_traceability_coverage: 1,
    bbox_citation_coverage: 1,
    element_lineage_coverage: 1,
    content_kind_hit_rate: 1,
    section_coverage: 1,
    passed: true,
    threshold_failures: [],
    failure_reason_counts: {},
    ingestion_quality: {
      document_count: 1,
      table_document_count: 0,
      figure_document_count: 0,
      long_document_count: 0,
      risk_counts: { high: 0, medium: 0 },
      warning_counts: {},
      parser_profile_counts: {},
    },
    case_results: [
      {
        case_id: "policy-approval-flow-basic",
        trace_id: "trace-eval",
        status: "success",
        retrieved_document_ids: ["doc-1"],
        relevant_document_ids: ["doc-1"],
        hit_document_ids: ["doc-1"],
        precision_at_k: 1,
        recall_at_k: 1,
        reciprocal_rank: 1,
        answer_keyword_hit: true,
        groundedness_passed: true,
        groundedness_score: 1,
        grounding_overlap_count: 2,
        grounding_answer_feature_count: 2,
        faithfulness: 1,
        context_precision: 1,
        context_recall: 1,
        response_relevancy: 1,
        noise_sensitivity: 1,
        citation_traceability_coverage: 1,
        bbox_citation_coverage: 1,
        element_lineage_coverage: 1,
        content_kind_hit_rate: 1,
        section_coverage: 1,
        guardrail_warnings: [],
        failure_reasons: [],
        diagnostics: {},
        elapsed_ms: 15,
        error_type: null,
        error_message: null,
      },
    ],
  };
}

function evaluationMetricsWithTimedOutCase() {
  const metrics = evaluationMetrics("balanced");
  const [success] = metrics.case_results;
  return {
    ...metrics,
    case_count: 2,
    error_count: 1,
    passed: false,
    failure_reason_counts: { case_error: 1 },
    case_results: [
      {
        ...success,
        case_id: "case-slow",
        trace_id: "trace-slow",
        status: "error",
        retrieved_document_ids: [],
        hit_document_ids: [],
        precision_at_k: 0,
        recall_at_k: 0,
        reciprocal_rank: 0,
        answer_keyword_hit: false,
        groundedness_passed: false,
        failure_reasons: ["case_error"],
        elapsed_ms: 300000,
        error_type: "TimeoutError",
        error_stage: "agentic_planning",
        error_message:
          "評価ケースの回答生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 検索の計画）。trace_id で監査ログを確認してください。",
      },
      success,
    ],
  };
}

// #541: JSON の未入力・形式のエラーは、押せないボタンと Banner ではなく欄の直下に出し、その欄へフォーカスする。
test("評価の JSON のエラーは欄の直下に出し、その欄へフォーカスする", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "balanced"),
    autoComplete: true,
  });

  await page.goto("/evaluation");
  const request = page.getByLabel("Golden set JSON");
  await request.fill("{");
  const run = page.getByRole("button", { name: "評価実行" });
  await expect(run).toBeEnabled();
  await run.click();
  await expect(page.locator("#evaluation-request-json-error")).toHaveText(
    "Golden set JSON は有効な JSON で入力してください。"
  );
  await expect(request).toHaveAttribute("aria-invalid", "true");
  await expect(request).toBeFocused();
  await request.fill('{"cases": []}');
  await expect(page.locator("#evaluation-request-json-error")).toHaveCount(0);
  await run.click();
  await expect(page.locator("#evaluation-request-json-error")).toHaveText(
    "Golden set JSON の cases を 1 件以上入力してください。"
  );

  await page.getByRole("button", { name: "サンプルを読み込む" }).click();
  const experiments = page.getByLabel("Experiments JSON");
  await experiments.fill("");
  await page.getByRole("button", { name: "比較実行" }).click();
  await expect(page.locator("#evaluation-experiments-json-error")).toHaveText("Experiments JSON を入力してください。");
  await expect(experiments).toBeFocused();
  expect(jobs.runPayloads).toHaveLength(0);
  await expectNoPageOverflow(page);
});
