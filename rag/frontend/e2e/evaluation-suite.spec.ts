import { expect, type Page, test } from "@playwright/test";

import { mockEvaluationJobs } from "./_evaluation-jobs";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockKnowledgeBases(page);
  await mockEvaluationSuiteSettings(page, "standard");
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`評価ページは既定の評価の基準を表示する (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });

    await page.goto("/evaluation");

    await expect(
      page.getByRole("heading", { name: "品質評価", level: 1 })
    ).toBeVisible();
    // 既定は「設定の既定に従う」で、現在のグローバル既定(標準)を表示する。
    await expect(page.getByText("設定の既定に従う(現在: 標準)")).toBeVisible();
    // 既定の基準(標準)の閾値プレビューが、指標の日本語名と百分率で見える(#591)。
    const preview = page.getByTestId("evaluation-suite-thresholds");
    await expect(preview.getByText("正解文書の再現率")).toBeVisible();
    await expect(preview.getByText("80%").first()).toBeVisible();
    await expect(preview.getByText("標準回答での合格")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "設定で既定の基準を変更" })
    ).toHaveAttribute("href", "/settings/evaluation");
    await expectNoPageOverflow(page);
  });
}

test("既定の基準のまま評価実行すると suite を送らず適用した基準を表示する", async ({
  page,
}) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "standard"),
    autoComplete: true,
  });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();

  await expect.poll(() => jobs.runPayloads[0] && "suite" in jobs.runPayloads[0]).toBe(false);
  await expect(page.getByText("評価の基準: 標準")).toBeVisible();
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
      runResult: () => evaluationMetrics("standard"),
    });

    await page.goto("/evaluation");
    await page.getByRole("button", { name: "評価実行" }).click();

    // 実行状況（#390）: 状態・件数・今のケース・経過時間（placement="job"）と取り消し。
    const panel = page.getByTestId("evaluation-run-job");
    await expect(panel.locator("[data-status-variant]")).toHaveText("実行中");
    await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");
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
    await expect(page.getByText("評価の基準: 標準")).toBeVisible();
    await expect(panel.locator("[data-status-variant]")).toHaveText("完了");
    await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("2 / 2 件（100%）");
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
    await expect(error).toContainText("工程: 根拠の検索と回答の生成");
    await expect(error).toContainText(
      "評価ケースの回答生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 根拠の検索と回答の生成）。"
    );
    await expectNoPageOverflow(page);
  });
}

test("実行中の評価は確認してから取り消せる", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("standard") });

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
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("standard") });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");

  // 戻っただけで評価を送り直さず、保存した job id でサーバーの状態を確かめる（workspace-state.md）。
  await page.reload();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");
  expect(jobs.runPayloads).toHaveLength(1);

  jobs.fail("run", "品質評価の実行に失敗しました（RuntimeError）。");
  const panel = page.getByTestId("evaluation-run-job");
  await expect(panel.locator("[data-status-variant]")).toHaveText("失敗");
  await expect(panel.getByText("評価を最後まで実行できませんでした。")).toBeVisible();
  await expect(panel.getByText("品質評価の実行に失敗しました（RuntimeError）。")).toBeVisible();
});

test("基準を選ぶと閾値プレビューを更新し suite を送る", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "standard"),
    autoComplete: true,
  });

  await page.goto("/evaluation");

  await page.getByRole("combobox", { name: "評価の基準" }).click();
  await page
    .getByRole("listbox", { name: "評価の基準" })
    .getByRole("option", { name: "厳格", exact: true })
    .click();

  // 厳格の閾値(Faithfulness 100%)がプレビューに出る。
  const preview = page.getByTestId("evaluation-suite-thresholds");
  await expect(preview.getByRole("listitem").filter({ hasText: "Faithfulness（根拠への忠実性）" })).toContainText(
    "100%"
  );

  await page.getByRole("button", { name: "評価実行" }).click();

  await expect.poll(() => jobs.runPayloads[0]?.suite).toBe("strict");
  await expect(page.getByText("評価の基準: 厳格")).toBeVisible();
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
  const thresholds = (strict: boolean) => ({
    context_recall: strict ? 0.9 : 0.8,
    mrr: strict ? 0.8 : 0.6,
    faithfulness: strict ? 0.8 : 0.7,
    citation_traceability_coverage: strict ? 0.95 : 0.9,
    claim_support_rate: strict ? 1 : 0.9,
    answer_keyword_hit_rate: strict ? 0.9 : 0.8,
    refusal_accuracy: strict ? 1 : 0.9,
    requirement_coverage: strict ? 0.9 : 0.8,
    answer_pass_rate: strict ? 0.8 : 0.7,
  });
  const specs = [
    { name: "standard", thresholds: thresholds(false) },
    { name: "strict", thresholds: thresholds(true) },
  ];
  const selected = specs.find((item) => item.name === suite) ?? specs[0];
  return {
    data: {
      suite,
      thresholds: selected.thresholds,
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
    case_count: 2,
    error_count: 0,
    evaluation_suite: suite,
    context_recall: 1,
    mrr: 0.5,
    faithfulness: 0.82,
    citation_traceability_coverage: 1,
    claim_support_rate: 1,
    answer_keyword_hit_rate: 1,
    refusal_accuracy: 1,
    requirement_coverage: 0.75,
    answer_pass_rate: 1,
    metric_case_counts: {
      context_recall: 1,
      mrr: 1,
      faithfulness: 1,
      citation_traceability_coverage: 1,
      claim_support_rate: 1,
      answer_keyword_hit_rate: 1,
      refusal_accuracy: 2,
      requirement_coverage: 1,
      answer_pass_rate: 1,
    },
    passed: true,
    threshold_failures: [],
    failure_reason_counts: {},
    case_results: [
      {
        case_id: "policy-approval-flow-basic",
        trace_id: "trace-eval",
        status: "success",
        retrieved_document_ids: ["doc-2", "doc-1"],
        relevant_document_ids: ["doc-1"],
        hit_document_ids: ["doc-1"],
        context_recall: 1,
        reciprocal_rank: 0.5,
        faithfulness: 0.82,
        grounding_overlap_count: 2,
        grounding_answer_feature_count: 2,
        citation_traceability_coverage: 1,
        answer_keyword_hit: true,
        abstained: false,
        refusal_correct: true,
        answer_evaluation: {
          status: "completed",
          passed: true,
          claims_supported: true,
          requirement_coverage: 0.75,
          missing_content: false,
          message: null,
        },
        guardrail_warnings: [],
        failure_reasons: [],
        diagnostics: {},
        elapsed_ms: 15,
        error_type: null,
        error_message: null,
      },
      {
        case_id: "out-of-scope-refusal",
        trace_id: "trace-refusal",
        status: "success",
        retrieved_document_ids: [],
        relevant_document_ids: [],
        hit_document_ids: [],
        context_recall: null,
        reciprocal_rank: null,
        faithfulness: null,
        grounding_overlap_count: 0,
        grounding_answer_feature_count: 0,
        citation_traceability_coverage: null,
        answer_keyword_hit: null,
        abstained: true,
        refusal_correct: true,
        answer_evaluation: null,
        guardrail_warnings: [],
        failure_reasons: [],
        diagnostics: {},
        elapsed_ms: 12,
        error_type: null,
        error_message: null,
      },
    ],
  };
}

function evaluationMetricsWithTimedOutCase() {
  const metrics = evaluationMetrics("standard");
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
        context_recall: null,
        reciprocal_rank: null,
        faithfulness: null,
        answer_keyword_hit: null,
        abstained: null,
        refusal_correct: null,
        answer_evaluation: null,
        failure_reasons: ["case_error"],
        elapsed_ms: 300000,
        error_type: "TimeoutError",
        error_stage: "answer",
        error_message:
          "評価ケースの回答生成が上限の 5 分以内に終わりませんでした（時間切れになった工程: 根拠の検索と回答の生成）。trace_id で監査ログを確認してください。",
      },
      success,
    ],
  };
}

// #541: JSON の未入力・形式のエラーは、押せないボタンと Banner ではなく欄の直下に出し、その欄へフォーカスする。
test("評価の JSON のエラーは欄の直下に出し、その欄へフォーカスする", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, {
    runResult: (payload) => evaluationMetrics((payload.suite as string) ?? "standard"),
    autoComplete: true,
  });

  await page.goto("/evaluation");
  const request = page.getByLabel("Golden set JSON");
  await request.fill("{");
  const run = page.getByRole("button", { name: "評価実行" });
  await expect(run).toBeEnabled();
  await run.click();
  await expect(request).toHaveAccessibleDescription(/Golden set JSON は有効な JSON で入力してください。/);
  await expect(request).toHaveAttribute("aria-invalid", "true");
  await expect(request).toBeFocused();
  await request.fill('{"cases": []}');
  await expect(request).not.toHaveAttribute("aria-invalid", "true");
  await expect(request).not.toHaveAccessibleDescription(/入力してください。/);
  await run.click();
  await expect(request).toHaveAccessibleDescription(/Golden set JSON の cases を 1 件以上入力してください。/);

  await page.getByRole("button", { name: "サンプルを読み込む" }).click();
  const experiments = page.getByLabel("Experiments JSON");
  await experiments.fill("");
  await page.getByRole("button", { name: "比較実行" }).click();
  await expect(experiments).toHaveAccessibleDescription(/Experiments JSON を入力してください。/);
  await expect(experiments).toBeFocused();
  expect(jobs.runPayloads).toHaveLength(0);
  await expectNoPageOverflow(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`評価結果は検索・根拠・回答の観点ごとに 9 つの指標とケースの判定を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockEvaluationJobs(page, {
      runResult: () => ({
        ...evaluationMetrics("standard"),
        passed: false,
        answer_pass_rate: null,
        metric_case_counts: { ...evaluationMetrics("standard").metric_case_counts, answer_pass_rate: 0 },
        threshold_failures: [{ metric: "mrr", actual: 0.5, threshold: 0.6 }],
        failure_reason_counts: { partial_recall: 1 },
      }),
      autoComplete: true,
    });

    await page.goto("/evaluation");
    await page.getByRole("button", { name: "評価実行" }).click();

    for (const [id, name] of [
      ["retrieval", "検索"],
      ["grounding", "根拠"],
      ["answer", "回答"],
    ]) {
      await expect(
        page.getByTestId(`evaluation-perspective-${id}`).getByRole("heading", { name, exact: true })
      ).toBeVisible();
    }
    const mrr = page.getByTestId("evaluation-metric-mrr");
    await expect(mrr).toContainText("50%");
    await expect(mrr).toContainText("対象 1 件");
    // 閾値未達はアイコンと文言付きのバッジで示す(色だけに頼らない)。
    await expect(mrr.locator("[data-status-variant]")).toHaveText("閾値未達");
    // 測れなかった指標は 0% ではなく「—」と理由を出す。
    const pass = page.getByTestId("evaluation-metric-answer_pass_rate");
    await expect(pass).toContainText("—");
    await expect(pass).toContainText("標準回答が必要");
    await expect(page.getByText("正解文書の順位(MRR): 50% / 60%")).toBeVisible();
    await expect(page.getByTestId("evaluation-failure-reasons")).toContainText(
      "正解の文書の一部だけ取れた: 1"
    );
    const table = page.getByTestId("evaluation-case-scroll-region");
    await expect(table.getByTestId("evaluation-case-judgement").first()).toContainText(
      "合格"
    );
    await expect(table.getByTestId("evaluation-case-answer").nth(1)).toContainText("拒答した");
    await expectNoPageOverflow(page);
  });
}

test("保存済みの古い評価の結果(削除した指標・基準)も表示を壊さない", async ({ page }) => {
  await mockEvaluationJobs(page, {
    runResult: () => ({
      case_count: 1,
      error_count: 0,
      evaluation_suite: "balanced",
      evaluated_k: 3,
      precision_at_k: 0.3333,
      recall_at_k: 1,
      mrr: 1,
      answer_keyword_hit_rate: 1,
      passed: false,
      threshold_failures: [{ metric: "section_coverage", actual: 0.5, threshold: 0.8 }],
      failure_reason_counts: { section_miss: 1 },
      case_results: [
        {
          case_id: "legacy-case",
          trace_id: "trace-legacy",
          status: "success",
          retrieved_document_ids: ["doc-1"],
          relevant_document_ids: ["doc-1"],
          hit_document_ids: ["doc-1"],
          reciprocal_rank: 1,
          answer_keyword_hit: true,
          guardrail_warnings: [],
          failure_reasons: ["section_miss"],
          diagnostics: {},
          elapsed_ms: 10,
          error_type: null,
          error_message: null,
        },
      ],
    }),
    autoComplete: true,
  });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();

  await expect(page.getByText("評価の基準: balanced")).toBeVisible();
  await expect(page.getByTestId("evaluation-metric-mrr")).toContainText("100%");
  await expect(page.getByTestId("evaluation-metric-refusal_accuracy")).toContainText("—");
  await expect(page.getByText("section_coverage: 50% / 80%")).toBeVisible();
  await expect(page.getByTestId("evaluation-failure-reasons")).toContainText("section_miss: 1");
  await expect(page.getByText("legacy-case")).toBeVisible();
});
