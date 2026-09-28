import type { Page } from "@playwright/test";

type JobKind = "run" | "compare";
type JobStatus = "RUNNING" | "SUCCEEDED" | "FAILED" | "CANCELLED";

interface MockJob {
  job_id: string;
  kind: JobKind;
  status: JobStatus;
  total_cases: number;
  completed_cases: number;
  current_case_id: string | null;
  current_experiment_id: string | null;
  payload: Record<string, unknown>;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

/**
 * 品質評価の job の API（#390。投入・状態の取得・取り消し）の mock。
 * `autoComplete` なら最初の状態の取得で完了にする。そうでなければ `complete` / `fail` を呼ぶまで実行中。
 */
export async function mockEvaluationJobs(
  page: Page,
  options: {
    runResult?: (payload: Record<string, unknown>) => unknown;
    compareResult?: (payload: Record<string, unknown>) => unknown;
    autoComplete?: boolean;
  }
) {
  const jobs = new Map<string, MockJob>();
  const runPayloads: Record<string, unknown>[] = [];
  const comparePayloads: Record<string, unknown>[] = [];
  const cancelled: string[] = [];
  let sequence = 0;

  const view = (job: MockJob) => {
    const succeeded = job.status === "SUCCEEDED";
    return {
      job_id: job.job_id,
      kind: job.kind,
      status: job.status,
      total_cases: job.total_cases,
      completed_cases: job.completed_cases,
      current_case_id: job.status === "RUNNING" ? job.current_case_id : null,
      current_experiment_id: job.status === "RUNNING" ? job.current_experiment_id : null,
      current_case_started_at: job.status === "RUNNING" ? job.started_at : null,
      time_limit_seconds: 3600,
      error_message: job.error_message,
      created_at: job.started_at,
      started_at: job.started_at,
      finished_at: job.finished_at,
      heartbeat_at: job.started_at,
      run_result: succeeded && job.kind === "run" ? options.runResult?.(job.payload) ?? null : null,
      compare_result:
        succeeded && job.kind === "compare" ? options.compareResult?.(job.payload) ?? null : null,
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
    if (request.method() === "POST" && (path === "run" || path === "compare")) {
      const payload = request.postDataJSON() as Record<string, unknown>;
      const cases = Array.isArray(payload.cases) ? (payload.cases as { id: string }[]) : [];
      const experiments = Array.isArray(payload.experiments)
        ? (payload.experiments as { id: string }[])
        : [];
      (path === "run" ? runPayloads : comparePayloads).push(payload);
      sequence += 1;
      const job: MockJob = {
        job_id: `job${path}${sequence}`,
        kind: path,
        status: "RUNNING",
        total_cases: path === "run" ? cases.length : cases.length * experiments.length,
        completed_cases: 0,
        current_case_id: cases[0]?.id ?? null,
        current_experiment_id: path === "compare" ? (experiments[0]?.id ?? null) : null,
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

  const latest = (kind: JobKind) => [...jobs.values()].reverse().find((job) => job.kind === kind);
  return {
    runPayloads,
    comparePayloads,
    cancelled,
    /** 実行中の job を完了にする（次の状態の取得で画面に反映される）。 */
    complete(kind: JobKind = "run") {
      const job = latest(kind);
      if (job) finish(job, "SUCCEEDED");
    },
    fail(kind: JobKind, message: string) {
      const job = latest(kind);
      if (job) finish(job, "FAILED", message);
    },
  };
}
