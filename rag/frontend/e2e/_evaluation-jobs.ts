import { expect, type Page } from "@playwright/test";

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
      current_experiment_id: null,
      current_case_started_at: job.status === "RUNNING" ? job.started_at : null,
      time_limit_seconds: 3600,
      error_message: job.error_message,
      created_at: job.started_at,
      started_at: job.started_at,
      finished_at: job.finished_at,
      heartbeat_at: job.started_at,
      run_result: succeeded ? options.runResult?.(job.payload) ?? null : null,
    };
  };
  const finish = (job: MockJob, status: JobStatus, message: string | null = null) => {
    job.status = status;
    job.error_message = message;
    job.finished_at = new Date().toISOString();
    if (status === "SUCCEEDED") job.completed_cases = job.total_cases;
  };
  const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

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
        expects_answer: true,
      },
      {
        row: 3,
        case_id: "row-3",
        question: "社員食堂の来月の献立を教えてください。",
        evidence: [],
        standard_answer_preview: null,
        has_standard_answer: false,
        expects_answer: false,
      },
    ],
    errors: [],
    error_count: 0,
    knowledge_base_ids: ["kb-1"],
    document_names: { "doc-1": "経費精算規程.pdf", "doc-2": "出張旅費規程.docx" },
    max_rows: 200,
    max_bytes: 2 * 1024 * 1024,
    ...overrides,
  };
}

/**
 * 評価リクエストの Excel の検証の API（`POST /api/evaluation/cases/import`）の mock。送った KB の ID を記録し、
 * 結果の `knowledge_base_ids` は送った値にする（画面は KB を変えたら検証し直す）。
 */
export async function mockEvaluationImport(
  page: Page,
  result: (knowledgeBaseIds: string[]) => Record<string, unknown> = () => evaluationImportResult()
) {
  const requests: string[][] = [];
  await page.route("**/api/evaluation/cases/import", async (route) => {
    const body = route.request().postDataBuffer()?.toString("utf8") ?? "";
    const ids = [...body.matchAll(/name="knowledge_base_ids"\r\n\r\n([^\r\n]+)/g)].map((match) => match[1] ?? "");
    requests.push(ids);
    await route.fulfill({
      json: { data: { ...result(ids), knowledge_base_ids: ids }, error_messages: [], warning_messages: [] },
    });
  });
  return { requests };
}

/** 品質評価の画面でナレッジベース（社内規程）を選ぶ。 */
export async function chooseEvaluationKnowledgeBase(page: Page, name: RegExp = /社内規程/) {
  const combo = page.getByRole("combobox", { name: "ナレッジベース" });
  const listbox = page.getByRole("listbox", { name: "ナレッジベース" });
  // 2 回目以降は入力欄にフォーカスが残り、押しても開かないことがあるので、開閉のボタンで開く。
  if (!(await listbox.isVisible())) await page.getByRole("button", { name: "ナレッジベースの一覧を開閉" }).click();
  await listbox.getByRole("option", { name }).click();
  await combo.press("Escape");
}

/** 評価リクエストの Excel を選ぶ（中身は検証の API の mock が返すので、何でもよい）。 */
export async function uploadEvaluationExcel(page: Page, name = "cases.xlsx") {
  await page.getByTestId("evaluation-request-file").setInputFiles({
    name,
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    buffer: Buffer.from("xlsx"),
  });
}

/** 評価を実行できる状態にする（KB を選び、Excel を検証する）。 */
export async function prepareEvaluationRun(page: Page) {
  const imports = await mockEvaluationImport(page);
  await chooseEvaluationKnowledgeBase(page);
  await uploadEvaluationExcel(page);
  await expect(page.getByTestId("evaluation-import-ready")).toBeVisible();
  return imports;
}
