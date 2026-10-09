/**
 * e2e の mock のジョブの処理の段階のイベント（`progress_events`。3 製品共通の契約。#1359）。
 *
 * backend（`_record_job_progress`）と同じ順に記録する: 開始待ちと 5 段階を待機中で出し、前の段階から順に
 * 実行中 → 完了・失敗・未実行にし、最後に終端を出す。後の状態のイベントは前の状態のイベントの続き（同じ番号は
 * 同じ内容）になるので、polling の応答ごとに作り直してよい。
 */

export const JOB_PROGRESS_STEPS = [
  "queue",
  "prepare_context",
  "generate_sql",
  "safety_check",
  "execute_sql",
  "format_results",
] as const;

export type JobProgressStep = (typeof JOB_PROGRESS_STEPS)[number];
type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";
type Params = Record<string, string | number | boolean>;

export interface JobProgressStepSpec {
  status: StepStatus;
  startedAt?: string;
  finishedAt?: string;
  params?: Params;
}

export interface JobProgressSpec {
  /** 段階の状態（書かない段階は待機中）。 */
  steps: Partial<Record<JobProgressStep, StepStatus | JobProgressStepSpec>>;
  terminal?: "done" | "failed" | "cancelled";
  /** 時刻を書かない段階の開始・終了の時刻。 */
  at: string;
  attempt?: number;
}

/** 段階の状態からイベントの一覧を作る。 */
export function jobProgressEvents(jobId: string, spec: JobProgressSpec): Record<string, unknown>[] {
  const events: Record<string, unknown>[] = [];
  const push = (event: Record<string, unknown>) =>
    events.push({
      schema_version: 1,
      seq: events.length + 1,
      target_id: jobId,
      attempt: spec.attempt ?? 0,
      emitted_at: spec.at,
      ...event,
    });
  for (const step of JOB_PROGRESS_STEPS) push({ type: "step", step_id: step, status: "pending" });
  for (const step of JOB_PROGRESS_STEPS) {
    const raw = spec.steps[step];
    if (!raw) continue;
    const state: JobProgressStepSpec = typeof raw === "string" ? { status: raw } : raw;
    if (state.status === "pending") continue;
    const params = state.params ? { params: state.params } : {};
    // 始まらずに未実行になった段階（終端の後ろの段階）には時刻を付けない。
    if (state.status === "skipped" && !state.startedAt && !state.params?.stopped) {
      push({ type: "step", step_id: step, status: "skipped", ...params });
      continue;
    }
    const startedAt = state.startedAt ?? spec.at;
    push({ type: "step", step_id: step, status: "running", started_at: startedAt, ...params });
    if (state.status === "running") continue;
    push({
      type: "step",
      step_id: step,
      status: state.status,
      started_at: startedAt,
      finished_at: state.finishedAt ?? spec.at,
      ...params,
    });
  }
  if (spec.terminal) push({ type: "terminal", status: spec.terminal });
  return events;
}

/** 完了したジョブ（すべての段階が完了）のイベント。 */
export function doneJobProgressEvents(jobId: string, at: string): Record<string, unknown>[] {
  return jobProgressEvents(jobId, {
    at,
    terminal: "done",
    steps: Object.fromEntries(JOB_PROGRESS_STEPS.map((step) => [step, "done"])),
  });
}

const JOB_STAGES = JOB_PROGRESS_STEPS.slice(1);

interface MockTurn {
  job_id: string;
  status: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  engine?: unknown;
  error_code?: unknown;
  steps?: unknown[];
  result?: unknown;
  last_execution?: unknown;
  /** 安全性の確認の完了で backend が記録する参照した表（結果が届く前の段階の mock 用。無ければ結果の値）。 */
  referenced_tables?: unknown;
  progress_events?: unknown;
  attempt?: unknown;
}

interface MockStep {
  stage: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function jobStageStatus(turn: MockTurn, step: MockStep | undefined, index: number, reported: number): StepStatus {
  if (turn.status === "pending") return "pending";
  if (step) return step.status === "error" ? "failed" : (step.status as StepStatus);
  if (turn.status === "done") return "done";
  // 段階の無いジョブ（古い mock）は、最初の段階を今の段階にする。
  if (index === 0 && reported === 0) return turn.status === "error" ? "failed" : "running";
  return "pending";
}

/**
 * mock のジョブ（状態・`steps`）から、backend が記録するのと同じ処理の段階のイベントを作る（mock の backend）。
 *
 * - 開始待ち: 待機中のジョブは実行中、worker が始めたら完了。始める前の失敗は失敗、停止は未実行と停止の印。
 * - 段階: `steps` の状態（`error` は失敗）。書かれていない段階は、完了したジョブなら完了、それ以外は待機中。
 * - 終端のジョブは実行中の段階を失敗（停止なら停止の印を付けて未実行）、待機中の段階を未実行にする。
 * - 終端でないのに実行中・待機中の段階が無ければ、最後の段階を実行中にする（backend は保存まで実行中）。
 * - 補足の値: 生成方法、参照した表（安全性の確認の完了）、取得した行数（実行の完了）。
 */
export function turnProgressEvents(mock: object): Record<string, unknown>[] {
  const turn = mock as MockTurn;
  const at = turn.created_at;
  const terminal = turn.status === "done" || turn.status === "error";
  const cancelled = turn.error_code === "JOB_CANCELLED";
  const reported = new Map(((turn.steps ?? []) as MockStep[]).map((step) => [step.stage, step] as const));
  const steps: Partial<Record<JobProgressStep, JobProgressStepSpec>> = {};
  steps.queue =
    turn.status === "pending"
      ? { status: "running", startedAt: at }
      : turn.started_at || turn.status !== "error"
        ? { status: "done", startedAt: at, finishedAt: turn.started_at ?? at }
        : cancelled
          ? { status: "skipped", startedAt: at, params: { stopped: true } }
          : { status: "failed", startedAt: at };
  const lastDone = JOB_STAGES.reduce(
    (last, stage, index) => (reported.get(stage)?.status === "done" ? index : last),
    -1,
  );
  JOB_STAGES.forEach((stage, index) => {
    const step = reported.get(stage);
    let status = jobStageStatus(turn, step, index, reported.size);
    // 後の段階が完了していれば、前の段階も完了している。
    if (index < lastDone && (status === "pending" || status === "running")) status = "done";
    const params: Params = {};
    if (stage === "generate_sql" && typeof turn.engine === "string") params.engine = turn.engine;
    if (stage === "safety_check" && status === "done") {
      const tables = turn.referenced_tables ?? record(record(turn.result).safety).referenced_tables;
      if (Array.isArray(tables) && tables.length > 0) {
        params.tables = tables.slice(0, 3).join(", ");
        params.table_count = tables.length;
      }
    }
    if (stage === "execute_sql" && status === "done") {
      const rows = record(turn.last_execution).row_count;
      if (typeof rows === "number") params.rows = rows;
    }
    if (terminal && (status === "running" || (cancelled && status === "failed"))) {
      if (cancelled) {
        params.stopped = true;
        status = "skipped";
      } else status = "failed";
    } else if (terminal && status === "pending") status = "skipped";
    const started = step?.started_at ?? at;
    steps[stage] = {
      status,
      startedAt: status === "skipped" && !params.stopped ? undefined : started,
      finishedAt: step?.finished_at ?? turn.finished_at ?? at,
      params: Object.keys(params).length > 0 ? params : undefined,
    };
  });
  const current = Object.values(steps).some((step) => step?.status === "running" || step?.status === "pending");
  if (!terminal && !current && steps.format_results) steps.format_results = { ...steps.format_results, status: "running" };
  const attempt = typeof turn.attempt === "number" ? Math.max(0, turn.attempt - 1) : 0;
  return jobProgressEvents(turn.job_id, {
    at,
    attempt,
    steps,
    terminal: terminal ? (turn.status === "done" ? "done" : cancelled ? "cancelled" : "failed") : undefined,
  });
}

/** mock の会話のターンに処理の段階のイベントを足す（テストが自分で書いたイベントはそのまま）。 */
export function withJobProgress<T extends object>(turn: T): T {
  const mock = turn as unknown as MockTurn;
  return mock.progress_events ? turn : { ...turn, progress_events: turnProgressEvents(turn) };
}
