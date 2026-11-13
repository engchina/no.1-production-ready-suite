import { expect, type Locator, type Page } from "@playwright/test";

/** 条件カードと同じテーマの外枠を持ち、結果だけ背景へ裸で出ない。 */
export async function expectEvaluationCard(page: Page, panel: Locator) {
  const frame = (element: HTMLElement) => {
    const style = getComputedStyle(element);
    return {
      background: style.backgroundColor,
      border: style.borderTopWidth,
      radius: style.borderTopLeftRadius,
      shadow: style.boxShadow,
    };
  };
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("role", "region");
  const actual = await panel.evaluate(frame);
  expect(actual).toEqual(await page.getByTestId("evaluation-conditions").evaluate(frame));
  expect(Number.parseFloat(actual.border)).toBeGreaterThan(0);
  expect(Number.parseFloat(actual.radius)).toBeGreaterThan(0);
}

type JobStatus = "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

interface MockJob {
  job_id: string;
  status: JobStatus;
  total_cases: number;
  completed_cases: number;
  current_case_id: string | null;
  payload: Record<string, unknown>;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

/**
 * 品質評価の job の API（#390。投入・状態の取得・取り消し）の mock。画面は評価（run）だけを投入する（#2065）。
 * `autoComplete` なら最初の状態の取得で完了にする。そうでなければ `complete` / `fail` を呼ぶまで実行中。
 */
export async function mockEvaluationJobs(
  page: Page,
  options: {
    runResult?: (payload: Record<string, unknown>) => unknown;
    partialResult?: (payload: Record<string, unknown>) => unknown;
    autoComplete?: boolean;
  }
) {
  const jobs = new Map<string, MockJob>();
  const runPayloads: Record<string, unknown>[] = [];
  const cancelled: string[] = [];
  let sequence = 0;

  const view = (job: MockJob) => {
    const succeeded = job.status === "SUCCEEDED";
    return {
      job_id: job.job_id,
      kind: "run",
      status: job.status,
      total_cases: job.total_cases,
      completed_cases: job.completed_cases,
      current_case_id: job.status === "RUNNING" ? job.current_case_id : null,
      current_case_started_at: job.status === "RUNNING" ? job.started_at : null,
      time_limit_seconds: 3600,
      error_message: job.error_message,
      created_at: job.started_at,
      started_at: job.started_at,
      finished_at: job.finished_at,
      heartbeat_at: job.started_at,
      run_result: succeeded ? options.runResult?.(job.payload) ?? null : null,
      partial_result:
        !succeeded && job.status !== "RUNNING"
          ? options.partialResult?.(job.payload) ?? null
          : null,
      source_workbook_id: (job.payload.source_workbook_id as string | undefined) ?? null,
      search_answer_profile_id: (job.payload.search_answer_profile_id as string | undefined) ?? null,
      compare_search_answer_profile_id:
        (job.payload.compare_search_answer_profile_id as string | undefined) ?? null,
      search_answer_profile_name: null,
      compare_search_answer_profile_name: null,
      actor_display_name: "ローカル利用者",
      owned: true,
      can_cancel: true,
    };
  };
  const summary = (job: MockJob) => {
    const detail = view(job);
    const result = detail.run_result as { passed?: boolean | null } | null;
    return {
      job_id: job.job_id,
      status: job.status,
      case_count: job.total_cases,
      total_cases: job.total_cases,
      completed_cases: job.completed_cases,
      created_at: job.started_at,
      finished_at: job.finished_at,
      passed: result?.passed ?? null,
      search_answer_profile_id: detail.search_answer_profile_id,
      compare_search_answer_profile_id: detail.compare_search_answer_profile_id,
      search_answer_profile_name: null,
      compare_search_answer_profile_name: null,
      actor_display_name: detail.actor_display_name,
      owned: true,
      can_cancel: true,
    };
  };
  const finish = (job: MockJob, status: JobStatus, message: string | null = null) => {
    job.status = status;
    job.error_message = message;
    job.finished_at = new Date().toISOString();
    if (status === "SUCCEEDED") job.completed_cases = job.total_cases;
  };
  const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

  // 過去の評価の一覧（#4811）。投入した job を新しい順に返す。
  await page.route(isEvaluationJobListUrl, async (route) => {
    const items = [...jobs.values()].reverse().map(summary);
    await route.fulfill({ json: envelope(evaluationJobPage(items)) });
  });
  await page.route("**/api/evaluation/jobs/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace(/^.*\/api\/evaluation\/jobs\//, "");
    if (request.method() === "POST" && path === "run") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      const cases = Array.isArray(payload.cases) ? (payload.cases as { id: string }[]) : [];
      runPayloads.push(payload);
      sequence += 1;
      const job: MockJob = {
        job_id: `jobrun${sequence}`,
        status: "RUNNING",
        total_cases: cases.length,
        completed_cases: 0,
        current_case_id: cases[0]?.id ?? null,
        payload,
        error_message: null,
        started_at: new Date().toISOString(),
        finished_at: null,
      };
      jobs.set(job.job_id, job);
      await route.fulfill({ status: 202, json: envelope(view(job)) });
      return;
    }
    const [jobId, action] = path.split("/");
    const job = jobs.get(jobId);
    if (!job) {
      await route.fulfill({
        status: 404,
        json: { data: null, error_messages: ["品質評価の job が見つかりません。"] },
      });
      return;
    }
    if (request.method() === "POST" && action === "cancel") {
      cancelled.push(jobId);
      if (job.status === "RUNNING") finish(job, "CANCELLED", "利用者の操作で品質評価を取り消しました。");
      await route.fulfill({ json: envelope(view(job)) });
      return;
    }
    if (options.autoComplete && job.status === "RUNNING") finish(job, "SUCCEEDED");
    await route.fulfill({ json: envelope(view(job)) });
  });

  const latest = () => [...jobs.values()].at(-1);
  return {
    runPayloads,
    cancelled,
    /** 実行中の job を完了にする（次の状態の取得で画面に反映される）。 */
    complete() {
      const job = latest();
      if (job) finish(job, "SUCCEEDED");
    },
    fail(message: string) {
      const job = latest();
      if (job) finish(job, "FAILED", message);
    },
  };
}

/** 評価リクエストの Excel の検証の結果（#2065。合成の社内規程の内容）。 */
export function evaluationImportResult(overrides: Record<string, unknown> = {}) {
  return {
    file_name: "cases.xlsx",
    row_count: 2,
    cases: [
      {
        id: "row-2",
        query: "経費精算の締め日はいつですか？",
        relevant_document_ids: ["doc-1", "doc-2"],
        required_evidence: [
          { id: "e1", document_id: "doc-1", page_start: 3, label: "経費精算規程.pdf | p.3" },
        ],
        standard_answer: "毎月 25 日が締め日です。",
      },
      { id: "row-3", query: "社員食堂の来月の献立を教えてください。" },
    ],
    rows: [
      {
        row: 2,
        case_id: "row-2",
        question: "経費精算の締め日はいつですか？",
        evidence: ["経費精算規程.pdf | p.3", "出張旅費規程.docx"],
        standard_answer_preview: "毎月 25 日が締め日です。",
        has_standard_answer: true,
        expected_response_decision: "answer",
      },
      {
        row: 3,
        case_id: "row-3",
        question: "社員食堂の来月の献立を教えてください。",
        evidence: [],
        standard_answer_preview: null,
        has_standard_answer: false,
        expected_response_decision: "hold",
      },
    ],
    errors: [],
    error_count: 0,
    warnings: [],
    warning_count: 0,
    search_answer_profile_id: "profile-base",
    knowledge_base_ids: ["kb-1"],
    document_names: { "doc-1": "経費精算規程.pdf", "doc-2": "出張旅費規程.docx" },
    max_rows: 200,
    max_bytes: 10 * 1024 * 1024,
    ...overrides,
  };
}

/**
 * 評価リクエストの Excel の検証の API（`POST /api/evaluation/cases/import`）の mock。送ったプロファイルの ID を記録し、
 * 結果の `search_answer_profile_id` は送った値にする（変更時は再検証する）。
 */
export async function mockEvaluationImport(
  page: Page,
  result: (knowledgeBaseIds: string[]) => Record<string, unknown> = () => evaluationImportResult()
) {
  const requests: string[][] = [];
  await page.route("**/api/evaluation/cases/import", async (route) => {
    const body = route.request().postDataBuffer()?.toString("utf8") ?? "";
    const ids = [...body.matchAll(/name="search_answer_profile_id"\r\n\r\n([^\r\n]+)/g)].map((match) => match[1] ?? "");
    requests.push(ids);
    await route.fulfill({
      json: { data: { ...result(ids), search_answer_profile_id: ids[0], knowledge_base_ids: ["kb-1"] }, error_messages: [], warning_messages: [] },
    });
  });
  return { requests };
}

/** 品質評価で主条件の検索・回答プロファイルを一つ選ぶ。 */
export async function chooseEvaluationProfile(page: Page, name: RegExp = /プロファイル A/) {
  await page.locator("#evaluation-profile").click();
  await page.getByRole("listbox").getByRole("option", { name }).click();
}

/** 評価ページの主条件・比較対象の合成プロファイル。 */
export async function mockEvaluationProfiles(page: Page) {
  await page.route("**/api/evaluation/models", route => route.fulfill({ json: { data: [], error_messages: [], warning_messages: [] } }));
  await page.route("**/api/search-answer-profiles**", route => route.fulfill({json: {
    data: {items: ["A", "B"].map((name, i) => ({
      id: i === 0 ? "profile-base" : "profile-1", name: `プロファイル ${name}`,
      description: "", status: "ACTIVE", knowledge_base_count: 1,
      created_at: "2026-06-15T00:00:00Z", updated_at: "2026-06-15T00:00:00Z", archived_at: null,
    })), total: 2, limit: 50, offset: 0, has_next: false}, error_messages: [], warning_messages: [],
  }}));
}

/** 評価リクエストの Excel を選ぶ（中身は検証の API の mock が返すので、何でもよい）。 */
export async function uploadEvaluationExcel(page: Page, name = "cases.xlsx") {
  await page.getByTestId("evaluation-request-file").setInputFiles({
    name,
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("xlsx"),
  });
}

/** 評価を実行できる状態にする（プロファイルを選び、Excel を検証する）。 */
export async function prepareEvaluationRun(
  page: Page,
  importResult?: () => Record<string, unknown>
) {
  await mockEvaluationProfiles(page);
  await page.reload();
  const imports = await mockEvaluationImport(page, importResult);
  await chooseEvaluationProfile(page);
  await uploadEvaluationExcel(page);
  await expect(page.getByTestId("evaluation-import-ready")).toBeVisible();
  return imports;
}

/** 過去の評価の一覧の API の URL か（query の付いた `/api/evaluation/jobs`）。 */
export function isEvaluationJobListUrl(url: URL): boolean {
  return url.pathname.endsWith("/api/evaluation/jobs");
}

/** 過去の評価の一覧の 1 ページ（#4811。offset 型の `Page`）。 */
export function evaluationJobPage(items: Record<string, unknown>[], offset = 0, limit = 25) {
  return {
    items: items.slice(offset, offset + limit),
    total: items.length,
    limit,
    offset,
    has_next: offset + limit < items.length,
  };
}

/** 過去の評価の一覧の API（`GET /api/evaluation/jobs`）の mock。既定は空（評価を投入する mock はあとから上書きする）。 */
export async function mockEvaluationJobList(page: Page, items: Record<string, unknown>[] = []) {
  const requests: URL[] = [];
  await page.route(isEvaluationJobListUrl, async (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const limit = Number(url.searchParams.get("limit") ?? 25);
    await route.fulfill({
      json: { data: evaluationJobPage(items, offset, limit), error_messages: [], warning_messages: [] },
    });
  });
  return { requests };
}
