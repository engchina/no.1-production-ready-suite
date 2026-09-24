import { expect, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

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
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
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
  let runPayload: Record<string, unknown> | null = null;
  await page.route("**/api/evaluation/run", async (route) => {
    runPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        data: evaluationMetrics((runPayload.suite as string) ?? "balanced"),
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/evaluation");
  await page.getByRole("button", { name: "評価実行" }).click();

  await expect.poll(() => runPayload && "suite" in runPayload).toBe(false);
  await expect(page.getByText("適用スイート: バランス")).toBeVisible();
});

test("スイートを選ぶと閾値プレビューを更新し suite を送る", async ({ page }) => {
  let runPayload: Record<string, unknown> | null = null;
  await page.route("**/api/evaluation/run", async (route) => {
    runPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      json: {
        data: evaluationMetrics((runPayload.suite as string) ?? "balanced"),
        error_messages: [],
        warning_messages: [],
      },
    });
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

  await expect.poll(() => runPayload?.suite).toBe("strict_ci");
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
