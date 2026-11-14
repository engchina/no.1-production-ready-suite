/**
 * 品質評価の過去の評価の一覧と URL での再表示（#4811）。合成の評価で、新しい browser context から
 * 一覧と URL の両方で保存済みの結果を開けること、見えない ID では中身を出さないことを確かめる。
 */
import { expect, type Browser, type Page, test, type TestInfo } from "./fixtures/test";

import { evaluationJobPage, isEvaluationJobListUrl, mockEvaluationProfiles } from "./_evaluation-jobs";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

function metrics(passed: boolean) {
  const base = {
    trace_id: "trace-1",
    status: "success",
    retrieved_document_ids: ["doc-1"],
    relevant_document_ids: ["doc-1"],
    hit_document_ids: ["doc-1"],
    required_evidence_recall: 1,
    mrr: 1,
    answer_correctness: passed ? 1 : 0.5,
    guardrail_warnings: [],
    missing_evidence: [],
    failure_reasons: [],
    diagnostics: {},
    elapsed_ms: 1,
    error_type: null,
    error_message: null,
  };
  return {
    case_count: 2,
    error_count: 0,
    evaluation_suite: "standard",
    passed,
    required_evidence_recall: 1,
    mrr: 1,
    answer_correctness: passed ? 1 : 0.5,
    metric_case_counts: { required_evidence_recall: 2, mrr: 2, answer_correctness: 2 },
    threshold_failures: passed ? [] : ["answer_correctness"],
    failure_reason_counts: {},
    category_breakdown: {},
    case_results: [
      { ...base, case_id: "row-2" },
      { ...base, case_id: "row-3", trace_id: "trace-2" },
    ],
  };
}

interface SyntheticJob {
  job_id: string;
  status: "RUNNING" | "SUCCEEDED";
  passed: boolean | null;
  /** 比べた条件の判定（比べた評価だけ。#5074）。 */
  comparePassed?: boolean;
  profile: string | null;
  actor: string | null;
  owned: boolean;
  created_at: string;
}

const JOBS: SyntheticJob[] = [
  { job_id: "jobmine", status: "SUCCEEDED", passed: true, profile: "プロファイル A", actor: "利用者 A", owned: true, created_at: "2026-11-09T03:00:00Z" },
  { job_id: "jobother", status: "SUCCEEDED", passed: false, comparePassed: false, profile: "プロファイル A", actor: "利用者 B", owned: false, created_at: "2026-11-09T02:00:00Z" },
  { job_id: "jobrunning", status: "RUNNING", passed: null, profile: "プロファイル A", actor: "利用者 B", owned: false, created_at: "2026-11-09T01:00:00Z" },
  { job_id: "joblegacy", status: "SUCCEEDED", passed: true, profile: null, actor: null, owned: true, created_at: "2026-11-08T01:00:00Z" },
];

function summary(job: SyntheticJob) {
  return {
    job_id: job.job_id,
    status: job.status,
    case_count: 2,
    total_cases: 2,
    completed_cases: job.status === "SUCCEEDED" ? 2 : 1,
    created_at: job.created_at,
    finished_at: job.status === "SUCCEEDED" ? job.created_at : null,
    passed: job.passed,
    compare_passed: job.comparePassed ?? null,
    search_answer_profile_id: job.profile ? "profile-base" : null,
    compare_search_answer_profile_id: job.comparePassed === undefined ? null : "profile-compare",
    search_answer_profile_name: job.profile,
    compare_search_answer_profile_name: job.comparePassed === undefined ? null : "プロファイル B",
    actor_display_name: job.actor,
    owned: job.owned,
    can_cancel: job.owned,
  };
}

function detail(job: SyntheticJob) {
  const { case_count: _count, passed: _passed, compare_passed: _comparePassed, ...rest } = summary(job);
  return {
    ...rest,
    kind: "run",
    current_case_id: job.status === "RUNNING" ? "row-3" : null,
    current_case_started_at: job.status === "RUNNING" ? job.created_at : null,
    time_limit_seconds: 3600,
    error_message: null,
    started_at: job.created_at,
    heartbeat_at: job.created_at,
    run_result: job.status === "SUCCEEDED" ? metrics(job.passed ?? true) : null,
    partial_result: null,
    source_workbook_id: null,
  };
}

/** 評価の画面の API の mock（見えない ID は 404。一覧・詳細の要求を記録する）。 */
async function mockEvaluationScreen(page: Page) {
  const detailRequests: string[] = [];
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockEvaluationProfiles(page);
  await page.route("**/api/settings/evaluation-suite", (route) =>
    route.fulfill({
      json: envelope({
        suite: "standard",
        thresholds: { answer_correctness: 0.9 },
        suites: [{ name: "standard", thresholds: { answer_correctness: 0.9 }, origin: "x", recommended_for: [], selected: true }],
        config_source: "runtime",
      }),
    })
  );
  await page.route(isEvaluationJobListUrl, (route) =>
    route.fulfill({ json: envelope(evaluationJobPage(JOBS.map(summary))) })
  );
  await page.route("**/api/evaluation/jobs/*", async (route) => {
    const jobId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1) ?? "");
    detailRequests.push(jobId);
    const job = JOBS.find((item) => item.job_id === jobId);
    if (!job) {
      await route.fulfill({ status: 404, json: { data: null, error_messages: ["品質評価の job が見つかりません。"] } });
      return;
    }
    await route.fulfill({ json: envelope(detail(job)) });
  });
  return { detailRequests };
}

async function newPage(browser: Browser, testInfo: TestInfo, width: number, height: number) {
  const context = await browser.newContext({
    baseURL: testInfo.project.use.baseURL,
    viewport: { width, height },
  });
  return context.newPage();
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(
    `別の browser context から一覧と URL で保存済みの結果を開き、見えない ID は中身を出さない (${viewport.name})`,
    { tag: "@responsive" },
    async ({ browser }, testInfo) => {
      // 1 つ目の context: 一覧から選ぶ（作業状態は空）。
      const page = await newPage(browser, testInfo, viewport.width, viewport.height);
      await mockEvaluationScreen(page);
      await page.goto("/evaluation");
      const history = page.getByTestId("evaluation-history");
      await expect(history.getByRole("heading", { name: "過去の評価" })).toBeVisible();
      await expect(history.getByTestId("evaluation-history-toolbar")).toContainText("全 4 件");
      const rows = history.getByRole("row");
      await expect(rows).toHaveCount(5);
      if (viewport.name === "desktop") {
        const header = history.getByRole("row").first();
        for (const column of ["実行日時", "検索・回答プロファイル", "状態", "件数", "全体の判定", "実行者"]) {
          await expect(header.getByRole("columnheader", { name: column })).toBeVisible();
        }
        await expect(page.getByTestId("evaluation-history-row-jobother")).toContainText("要改善");
        // 比べた評価は、比べた条件の判定も並べる（#5074）。
        await expect(page.getByTestId("evaluation-history-row-jobother")).toContainText("比較: 要改善");
        await expect(page.getByTestId("evaluation-history-row-jobmine")).not.toContainText("比較:");
        await expect(page.getByTestId("evaluation-history-row-jobother")).toContainText("利用者 B");
        // 実行者もプロファイルも分からない古い評価は「—」。
        await expect(page.getByTestId("evaluation-history-row-joblegacy")).toContainText("—");
      } else {
        // 375px は列を畳み、日時の下にプロファイル・状態・判定をまとめる。
        await expect(page.getByTestId("evaluation-history-row-jobother")).toContainText(
          "プロファイル A（比較: プロファイル B） · 完了 · 要改善 · 比較: 要改善"
        );
      }
      await expectNoPageOverflow(page);

      // 他の利用者の評価を選ぶ: URL が変わり、指標・全体の判定・ケース別の結果を出す。取り消しは出さない。
      await page.getByTestId("evaluation-history-row-jobother").getByRole("link").click();
      await expect(page).toHaveURL(/\/evaluation\?job=jobother$/);
      const panel = page.getByTestId("evaluation-run-job");
      await expect(panel).toContainText("実行者: 利用者 B");
      await expect(panel).toContainText("検索・回答プロファイル: プロファイル A");
      await expect(page.getByRole("button", { name: "取り消し" })).toHaveCount(0);
      const result = page.getByRole("region", { name: "評価結果", exact: true });
      await expect(result.getByText("要改善", { exact: true })).toBeVisible();
      const cases = page.getByTestId("evaluation-case-scroll-region");
      await expect(cases.getByText("2 行目")).toBeVisible();
      await expect(cases.getByText("3 行目")).toBeVisible();
      await expect(page.getByTestId("evaluation-history-row-jobother").getByRole("link")).toHaveAttribute("aria-current", "true");
      await expectNoPageOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`history-selected-${viewport.name}.png`), fullPage: true });

      // 他の利用者の実行中の評価も取り消しを出さない。
      await page.getByTestId("evaluation-history-row-jobrunning").getByRole("link").click();
      await expect(page).toHaveURL(/job=jobrunning$/);
      await expect(page.getByTestId("evaluation-run-job")).toContainText("実行中");
      await expect(page.getByRole("button", { name: "取り消し" })).toHaveCount(0);
      await page.context().close();

      // 2 つ目の context: URL で直接開く（作業状態に job id は無い）。
      const shared = await newPage(browser, testInfo, viewport.width, viewport.height);
      const sharedApi = await mockEvaluationScreen(shared);
      await shared.goto("/evaluation?job=jobmine");
      await expect(shared.getByTestId("evaluation-run-job")).toContainText("完了");
      await expect(
        shared.getByRole("region", { name: "評価結果", exact: true }).getByText("合格", { exact: true })
      ).toBeVisible();
      await expect(shared.getByTestId("evaluation-case-scroll-region").getByText("2 行目")).toBeVisible();
      await expect(shared.getByTestId("evaluation-history-row-jobmine").getByRole("link")).toHaveAttribute("aria-current", "true");
      // 自分の評価の結果は取り消し済みでなく、完了なので取り消しは出ない（実行中だけ）。
      await expect(shared.getByRole("button", { name: "取り消し" })).toHaveCount(0);

      // 見えない・無い ID: 「見つからない」だけを出し、結果を出さない。
      await shared.goto("/evaluation?job=jobhidden");
      await expect(shared.getByText("評価が見つかりません。")).toBeVisible();
      await expect(shared.getByTestId("evaluation-run-job")).toHaveCount(0);
      await expect(shared.getByTestId("evaluation-case-scroll-region")).toHaveCount(0);
      // ID の形でない値は問い合わせずに「見つからない」にする。
      await shared.goto(`/evaluation?job=${encodeURIComponent("../jobmine")}`);
      await expect(shared.getByText("評価が見つかりません。")).toBeVisible();
      await expect(shared.getByTestId("evaluation-run-job")).toHaveCount(0);
      expect(sharedApi.detailRequests).not.toContain("../jobmine");
      await expectNoPageOverflow(shared);
      await shared.context().close();
    }
  );
}

test("過去の評価の一覧は 25 件ずつページを送れる", async ({ page }) => {
  await mockEvaluationScreen(page);
  const many = Array.from({ length: 30 }, (_, index) =>
    summary({
      job_id: `jobpage${index}`,
      status: "SUCCEEDED",
      passed: true,
      profile: "プロファイル A",
      actor: "利用者 A",
      owned: true,
      created_at: new Date(Date.UTC(2026, 10, 9, 0, 59 - index)).toISOString(),
    })
  );
  const offsets: string[] = [];
  await page.route(isEvaluationJobListUrl, (route) => {
    const url = new URL(route.request().url());
    offsets.push(`${url.searchParams.get("offset")}:${url.searchParams.get("limit")}`);
    return route.fulfill({
      json: envelope(evaluationJobPage(many, Number(url.searchParams.get("offset")), Number(url.searchParams.get("limit")))),
    });
  });
  await page.goto("/evaluation");
  const history = page.getByTestId("evaluation-history");
  await expect(history.getByRole("row")).toHaveCount(26);
  await page.getByTestId("evaluation-history-pagination").getByRole("button", { name: /次/ }).click();
  await expect(history.getByRole("row")).toHaveCount(6);
  expect(offsets).toEqual(["0:25", "25:25"]);
});
