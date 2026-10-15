import { expect, type Page, test } from "./fixtures/test";

import {
  chooseEvaluationKnowledgeBase,
  evaluationImportResult,
  mockEvaluationImport,
  mockEvaluationJobs,
  prepareEvaluationRun,
  uploadEvaluationExcel,
} from "./_evaluation-jobs";
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
  test(
    `評価ページは評価の基準を表示だけし、ナレッジベースと評価リクエストの Excel を必須にする (${viewport.name})`,
    async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      await page.goto("/evaluation");

      await expect(page.getByRole("heading", { name: "品質評価", level: 1 })).toBeVisible();
      // 評価の基準は表示だけ（#2065）。選択欄・ラベルは無く、設定の既定と閾値と設定へのリンクを出す。
      const suite = page.getByTestId("evaluation-suite");
      await expect(suite.getByRole("combobox")).toHaveCount(0);
      await expect(suite.getByTestId("evaluation-suite-current")).toHaveText("設定の既定: 標準");
      const preview = page.getByTestId("evaluation-suite-thresholds");
      await expect(preview.getByText("正解文書の再現率")).toBeVisible();
      await expect(preview.getByText("80%").first()).toBeVisible();
      await expect(preview.getByText("標準回答での合格")).toBeVisible();
      await expect(page.getByRole("link", { name: "設定で既定の基準を変更" })).toHaveAttribute(
        "href",
        "/settings/evaluation"
      );
      // ナレッジベースと評価リクエストの Excel は必須（部品の「必須」のタグ）。
      const conditions = page.getByRole("form", { name: "評価の条件" });
      await expect(conditions.getByText("必須", { exact: true })).toHaveCount(2);
      await expect(page.getByRole("button", { name: "テンプレートをダウンロード" })).toBeVisible();
      // JSON の入力・設定比較・サンプルは無い。
      await expect(page.getByLabel("Golden set JSON")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "比較実行" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "サンプルを読み込む" })).toHaveCount(0);
      await expectNoPageOverflow(page);

      // 未選択のまま実行すると、ナレッジベースの欄の直下に出してフォーカスを移す。
      const run = page.getByRole("button", { name: "評価実行" });
      await expect(run).toBeEnabled();
      await run.click();
      const knowledgeBase = page.getByTestId("evaluation-knowledge-base");
      await expect(knowledgeBase.getByRole("alert")).toHaveText("ナレッジベースを 1 つ以上選んでください。");
      await expect(page.getByRole("combobox", { name: "ナレッジベース" })).toBeFocused();

      // ナレッジベースを選ぶとエラーは消え、次は Excel の欄に出す。
      await chooseEvaluationKnowledgeBase(page);
      await expect(knowledgeBase.getByRole("alert")).toHaveCount(0);
      await run.click();
      const dropzone = page.getByTestId("evaluation-request-dropzone");
      await expect(dropzone).toHaveAccessibleDescription(/評価リクエストの Excel をアップロードしてください。/);
      await expect(dropzone).toHaveAttribute("aria-invalid", "true");
      await expect(dropzone).toBeFocused();
      await expectNoPageOverflow(page);
    }
  );

  test(
    `Excel の誤りは行・列・内容の表で出し、評価を実行しない (${viewport.name})`,
    async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const jobs = await mockEvaluationJobs(page, { autoComplete: true });
      await mockEvaluationImport(page, () =>
        evaluationImportResult({
          cases: [],
          rows: [],
          error_count: 3,
          errors: [
            { row: null, column: null, message: "評価のケースは 200 行までです。" },
            { row: 3, column: "期待する根拠", message: "1 行目: 「無い規程.pdf」は選んだナレッジベースの文書にありません。" },
            { row: 4, column: "質問", message: "質問を入力してください。" },
          ],
        })
      );

      await page.goto("/evaluation");
      await chooseEvaluationKnowledgeBase(page);
      await uploadEvaluationExcel(page);

      const errors = page.getByTestId("evaluation-import-errors");
      await expect(errors).toContainText("cases.xlsx に 3 件の誤りがあります。");
      const table = errors.getByRole("table", { name: "Excel の誤り。スクロールできます。" });
      await expect(table.getByRole("row", { name: /3 行目・期待する根拠/ })).toContainText("無い規程.pdf");
      await expect(table.getByRole("row", { name: /4 行目・質問/ })).toContainText("質問を入力してください。");
      await expect(table.getByRole("row", { name: /ファイル全体/ })).toBeVisible();

      await page.getByRole("button", { name: "評価実行" }).click();
      await expect(page.getByTestId("evaluation-request-dropzone")).toHaveAccessibleDescription(
        /Excel の誤りを直して、もう一度アップロードしてください。/
      );
      expect(jobs.runPayloads).toHaveLength(0);
      await expectNoPageOverflow(page);
    }
  );
}

test("テンプレートをダウンロードできる", async ({ page }) => {
  await page.route("**/api/evaluation/cases/template", (route) =>
    route.fulfill({
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": 'attachment; filename="evaluation_request_template.xlsx"',
      },
      body: "xlsx",
    })
  );

  await page.goto("/evaluation");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "テンプレートをダウンロード" }).click();

  expect((await download).suggestedFilename()).toBe("evaluation_request_template.xlsx");
  await expect(page.getByText("テンプレートをダウンロードしました。")).toBeVisible();
});

test("Excel の形式でないファイルは検証に送らず欄の直下に出す", async ({ page }) => {
  const imports = await mockEvaluationImport(page);
  await page.goto("/evaluation");
  await chooseEvaluationKnowledgeBase(page);

  await uploadEvaluationExcel(page, "cases.csv");

  await expect(page.getByTestId("evaluation-request-dropzone")).toHaveAccessibleDescription(
    /Excel（.xlsx）のファイルを選んでください。/
  );
  expect(imports.requests).toHaveLength(0);
});

test("ナレッジベースより先に Excel を選ぶと、選んだ時点で検証し、変えたら検証し直す", async ({ page }) => {
  const imports = await mockEvaluationImport(page);
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({ json: knowledgeBasesEnvelope(["社内規程", "人事規程"]) });
  });
  await page.goto("/evaluation");

  await uploadEvaluationExcel(page);
  await expect(page.getByText("ナレッジベースを選ぶと、Excel を確かめます", { exact: false })).toBeVisible();
  expect(imports.requests).toHaveLength(0);

  await chooseEvaluationKnowledgeBase(page);
  await expect(page.getByTestId("evaluation-import-ready")).toContainText("cases.xlsx: 2 件のケースを評価できます。");
  expect(imports.requests).toEqual([["kb-1"]]);

  // 期待する根拠のファイル名は KB で照合するので、KB を変えたら同じファイルで検証し直す。
  await chooseEvaluationKnowledgeBase(page, /人事規程/);
  await expect.poll(() => imports.requests.length).toBe(2);
  expect(imports.requests[1]).toEqual(["kb-1", "kb-2"]);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(
    `検証した Excel は件数とプレビューを出し、評価の job は進捗・今の行・経過時間を出す (${viewport.name})`,
    async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const jobs = await mockEvaluationJobs(page, {
        runResult: () => evaluationMetrics("standard"),
      });

      await page.goto("/evaluation");
      await prepareEvaluationRun(page);

      const ready = page.getByTestId("evaluation-import-ready");
      await expect(ready).toContainText("cases.xlsx: 2 件のケースを評価できます。");
      await expect(ready).toContainText("答えるべき質問 1 件・資料に答えが無い質問 1 件・標準回答あり 1 件");
      const preview = page.getByTestId("evaluation-import-preview");
      await expect(preview.getByRole("row", { name: /経費精算の締め日/ })).toContainText("経費精算規程.pdf | p.3");
      await expect(preview.getByRole("row", { name: /社員食堂/ })).toContainText("回答を控えるのが正しい質問");
      await expectNoPageOverflow(page);

      await page.getByRole("button", { name: "評価実行" }).click();
      // 評価の基準は設定の既定（suite を送らない）、検索の対象は選んだ KB、ケースは検証の結果。
      await expect.poll(() => jobs.runPayloads.length).toBe(1);
      expect(jobs.runPayloads[0]).toEqual({
        cases: evaluationImportResult().cases,
        knowledge_base_ids: ["kb-1"],
      });

      // 実行状況（#390）: 状態・件数・今の行・経過時間（placement="job"）と取り消し。
      const panel = page.getByTestId("evaluation-run-job");
      await expect(panel.locator("[data-status-variant]")).toHaveText("実行中");
      await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");
      await expect(page.getByTestId("evaluation-run-job-current-case")).toHaveText("実行中のケース: 2 行目");
      const timing = page.getByTestId("evaluation-run-job-timing");
      await expect(timing).toHaveAttribute("data-processing-placement", "job");
      await expect(timing.getByRole("timer")).toHaveAccessibleName(/経過時間 \d{2}:\d{2}/);
      await expect(panel.getByRole("button", { name: "取り消し" })).toBeVisible();
      // 実行中は同じ評価を重ねて投入しない。
      await expect(page.getByRole("button", { name: "評価実行" })).toBeDisabled();
      await expect(page.getByTestId("evaluation-result-loading")).toBeVisible();
      await expectNoPageOverflow(page);

      jobs.complete();
      await expect(page.getByText("評価の基準: 標準")).toBeVisible();
      await expect(panel.locator("[data-status-variant]")).toHaveText("完了");
      await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("2 / 2 件（100%）");
      await expect(timing.getByRole("timer")).toHaveAccessibleName(/処理時間 \d{2}:\d{2}/);
      await expect(panel.getByRole("button", { name: "取り消し" })).toHaveCount(0);
      await expect(page.getByTestId("evaluation-result-loading")).toHaveCount(0);
      await expect(page.getByRole("button", { name: "評価実行" })).toBeEnabled();

      // 結果の表は Excel の行と質問、期待する根拠の照合の結果（見つかった / 見つからない）を出す。
      const table = page.getByTestId("evaluation-case-scroll-region");
      const first = table.getByRole("row", { name: /経費精算の締め日/ });
      await expect(first.getByTestId("evaluation-case-question")).toContainText("2 行目");
      const evidence = first.getByTestId("evaluation-case-evidence");
      await expect(evidence).toContainText("1 / 2 件");
      await expect(evidence.getByRole("listitem").filter({ hasText: "経費精算規程.pdf | p.3" })).toContainText(
        "見つかった"
      );
      await expect(evidence.getByRole("listitem").filter({ hasText: "出張旅費規程.docx" })).toContainText(
        "見つからない"
      );
      await expect(table.getByRole("row", { name: /社員食堂/ }).getByTestId("evaluation-case-answer")).toContainText(
        "回答を控えた"
      );
      await expectNoPageOverflow(page);
    }
  );

  test(`失敗したケースは理由と時間切れになった工程を表に出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockEvaluationJobs(page, {
      runResult: () => evaluationMetricsWithTimedOutCase(),
      autoComplete: true,
    });

    await page.goto("/evaluation");
    await prepareEvaluationRun(page);
    await page.getByRole("button", { name: "評価実行" }).click();

    const error = page.getByTestId("evaluation-case-error");
    await expect(page.getByTestId("evaluation-case-question").first()).toContainText("2 行目");
    await expect(error).toContainText("エラー");
    await expect(error).toContainText("工程: 根拠の検索と回答の生成");
    await expect(error).toContainText(
      "評価ケースの回答生成が上限の 10 分以内に終わりませんでした（時間切れになった工程: 根拠の検索と回答の生成）。"
    );
    await expectNoPageOverflow(page);
  });
}

test("実行中の評価は確認してから取り消せる", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("standard") });

  await page.goto("/evaluation");
  await prepareEvaluationRun(page);
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

test("再読込しても検証した Excel と実行中の評価の job の状態を表示し、失敗の理由を出す", async ({ page }) => {
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("standard") });

  await page.goto("/evaluation");
  const imports = await prepareEvaluationRun(page);
  await page.getByRole("button", { name: "評価実行" }).click();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");

  // 戻っただけで評価を送り直さず、保存した job id でサーバーの状態を確かめる（workspace-state.md）。
  // 検証した Excel（ケース）も残し、送り直さない。
  await page.reload();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");
  await expect(page.getByTestId("evaluation-import-ready")).toContainText("cases.xlsx: 2 件のケースを評価できます。");
  expect(jobs.runPayloads).toHaveLength(1);
  expect(imports.requests).toHaveLength(1);

  jobs.fail(
    "品質評価の実行中に予期しないエラーが発生したため、評価を中断しました。サーバーのログを確認して、もう一度実行してください。 エラー種別: RuntimeError"
  );
  const panel = page.getByTestId("evaluation-run-job");
  await expect(panel.locator("[data-status-variant]")).toHaveText("失敗");
  await expect(panel.getByText("評価を最後まで実行できませんでした。")).toBeVisible();
  // 本文は原因と対処だけにし、例外の種別は開いた「詳細」に出す（messaging.md §10.3）。
  await expect(
    panel.getByText(
      "品質評価の実行中に予期しないエラーが発生したため、評価を中断しました。サーバーのログを確認して、もう一度実行してください。",
      { exact: true }
    )
  ).toBeVisible();
  const details = panel.locator("details").filter({ hasText: "詳細" });
  await expect(details).toHaveAttribute("open", "");
  await expect(details).toContainText("エラー種別");
  await expect(details).toContainText("RuntimeError");
});

// #977: 状態の取得が retry を使い切って失敗しても、実行中の job の状態の取得を止めない（止めると「実行中」のまま）。
test("実行中の評価の状態の取得が一時的に失敗しても、取得を続けて完了を出す", async ({ page }) => {
  test.setTimeout(60_000);
  const jobs = await mockEvaluationJobs(page, { runResult: () => evaluationMetrics("standard") });
  await page.goto("/evaluation");
  await prepareEvaluationRun(page);
  await page.getByRole("button", { name: "評価実行" }).click();
  await expect(page.getByTestId("evaluation-run-job-count")).toHaveText("0 / 2 件（0%）");

  let failing = true;
  await page.route("**/api/evaluation/jobs/jobrun*", async (route) => {
    if (failing && route.request().method() === "GET") {
      await route.fulfill({ status: 503, json: { data: null, error_messages: ["一時的に利用できません。"] } });
      return;
    }
    await route.fallback();
  });
  // 1 回の取得の retry（3 回）を使い切ると、パネルの直下に取得できていないことを出す。
  const warning = page.getByText("評価の実行状況を取得できません。自動で取得し直しています。", { exact: false });
  await expect(warning).toBeVisible({ timeout: 20_000 });
  const panel = page.getByTestId("evaluation-run-job");
  await expect(panel.locator("[data-status-variant]")).toHaveText("実行中");
  await expectNoPageOverflow(page);

  failing = false;
  jobs.complete();
  await expect(panel.locator("[data-status-variant]")).toHaveText("完了", { timeout: 15_000 });
  await expect(warning).toHaveCount(0);
  await expect(page.getByTestId("evaluation-perspective-retrieval")).toBeVisible();
});

function knowledgeBasesEnvelope(names: string[]) {
  return {
    data: {
      items: names.map((name, index) => ({
        id: `kb-${index + 1}`,
        name,
        description: "",
        status: "ACTIVE",
        default_search_mode: "hybrid",
        document_count: 3,
        indexed_document_count: 3,
        error_document_count: 0,
        searchable_chunk_count: 16,
        created_at: "2026-06-15T00:00:00Z",
        updated_at: "2026-06-15T00:00:00Z",
        archived_at: null,
      })),
      total: names.length,
      limit: 50,
      offset: 0,
      has_next: false,
    },
    error_messages: [],
    warning_messages: [],
  };
}

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({ json: knowledgeBasesEnvelope(["社内規程"]) });
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
        case_id: "row-2",
        trace_id: "trace-eval",
        status: "success",
        retrieved_document_ids: ["doc-3", "doc-1"],
        relevant_document_ids: ["doc-1", "doc-2"],
        hit_document_ids: ["doc-1"],
        context_recall: 0.5,
        evidence_recall: 1,
        missing_evidence: [],
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
        case_id: "row-3",
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
  const [success, refusal] = metrics.case_results;
  return {
    ...metrics,
    case_count: 2,
    error_count: 1,
    passed: false,
    failure_reason_counts: { case_error: 1 },
    case_results: [
      {
        ...success,
        case_id: "row-2",
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
          "評価ケースの回答生成が上限の 10 分以内に終わりませんでした（時間切れになった工程: 根拠の検索と回答の生成）。trace_id で監査ログを確認してください。",
      },
      refusal,
    ],
  };
}

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
        // 分類ごとの内訳（#1226）。
        category_breakdown: {
          document_answerable: {
            case_count: 2,
            error_count: 1,
            answer_pass_rate: 0.5,
            answer_keyword_hit_rate: 1,
            abstain_rate: 0,
            refusal_correct_rate: 1,
          },
          knowledge_missing: {
            case_count: 1,
            error_count: 0,
            answer_pass_rate: null,
            answer_keyword_hit_rate: null,
            abstain_rate: 1,
            refusal_correct_rate: 1,
          },
        },
      }),
      autoComplete: true,
    });

    await page.goto("/evaluation");
    await prepareEvaluationRun(page);
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
    const categories = page.getByTestId("evaluation-category-breakdown");
    await expect(categories.getByRole("row", { name: /資料で答えられる/ })).toContainText("2（失敗 1）");
    await expect(categories.getByRole("row", { name: /資料で答えられる/ })).toContainText("50%");
    await expect(categories.getByRole("row", { name: /資料に答えが無い/ })).toContainText("—");
    const table = page.getByTestId("evaluation-case-scroll-region");
    await expect(table.getByTestId("evaluation-case-judgement").first()).toContainText(
      "合格"
    );
    await expect(table.getByTestId("evaluation-case-answer").nth(1)).toContainText("回答を控えた");
    // 業務支援の対応（#1231）は、対象のケースが無い評価では出さない。
    await expect(page.getByTestId("evaluation-perspective-handling")).toHaveCount(0);
    await expect(table.getByTestId("evaluation-case-handling")).toHaveCount(0);
    await expectNoPageOverflow(page);
  });

  test(`業務支援の対応の指標と、ケースごとの対応・不足の手順・危険な表現を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const base = evaluationMetrics("standard");
    await mockEvaluationJobs(page, {
      runResult: () => ({
        ...base,
        handling_accuracy: 0.5,
        step_order_score: 0.6,
        safe_answer_rate: 0.5,
        condition_coverage: null,
        metric_case_counts: {
          ...base.metric_case_counts,
          handling_accuracy: 2,
          step_order_score: 1,
          safe_answer_rate: 2,
          condition_coverage: 0,
        },
        failure_reason_counts: { step_missing: 1, forbidden_action: 1 },
        category_breakdown: {
          clarification_required: {
            case_count: 1,
            error_count: 0,
            answer_pass_rate: null,
            answer_keyword_hit_rate: null,
            abstain_rate: 0,
            refusal_correct_rate: 1,
            handling_correct_rate: 0,
            step_order_score: null,
            safe_answer_rate: 0,
          },
        },
        case_results: [
          {
            ...base.case_results[0],
            observed_outcome: "answered",
            outcome_source: "inferred",
            handling_correct: false,
            step_order_score: 0.6,
            missing_steps: ["テスト送信"],
            forbidden_checked: true,
            forbidden_hits: ["グループに付与してください"],
          },
          {
            ...base.case_results[1],
            observed_outcome: "insufficient_evidence",
            outcome_source: "explicit",
            handling_correct: true,
          },
        ],
      }),
      autoComplete: true,
    });

    await page.goto("/evaluation");
    await prepareEvaluationRun(page);
    await page.getByRole("button", { name: "評価実行" }).click();

    const handling = page.getByTestId("evaluation-perspective-handling");
    await expect(handling.getByRole("heading", { name: "対応", exact: true })).toBeVisible();
    await expect(page.getByTestId("evaluation-metric-handling_accuracy")).toContainText("50%");
    await expect(page.getByTestId("evaluation-metric-condition_coverage")).toContainText("—");
    await expect(page.getByTestId("evaluation-failure-reasons")).toContainText("危険な操作を提示: 1");
    const categories = page.getByTestId("evaluation-category-breakdown");
    await expect(categories.getByRole("columnheader", { name: "危険な回答の無さ" })).toBeAttached();
    const table = page.getByTestId("evaluation-case-scroll-region");
    const first = table.getByTestId("evaluation-case-handling").first();
    await expect(first).toContainText("回答（推定）");
    await expect(first).toContainText("期待と違う");
    await expect(first).toContainText("不足・順序違いの手順: テスト送信");
    await expect(first).toContainText("危険な表現: グループに付与してください");
    await expect(table.getByTestId("evaluation-case-handling").nth(1)).toContainText("根拠不足（回答を控えた）");
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
  await prepareEvaluationRun(page);
  await page.getByRole("button", { name: "評価実行" }).click();

  await expect(page.getByText("評価の基準: balanced")).toBeVisible();
  await expect(page.getByTestId("evaluation-metric-mrr")).toContainText("100%");
  await expect(page.getByTestId("evaluation-metric-refusal_accuracy")).toContainText("—");
  await expect(page.getByText("section_coverage: 50% / 80%")).toBeVisible();
  await expect(page.getByTestId("evaluation-failure-reasons")).toContainText("section_miss: 1");
  await expect(page.getByText("legacy-case")).toBeVisible();
});
