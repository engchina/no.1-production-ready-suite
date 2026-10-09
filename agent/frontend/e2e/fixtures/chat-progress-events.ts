/**
 * e2e の mock の Run が持つ、チャットの処理の段階のイベント（3 製品共通の契約。#1359）。
 *
 * backend は Run の状態から段階のイベントを記録する（`app/features/agent/chat_progress.py`）。e2e は実 backend を
 * 起動しないので、spec が `ProgressLog` で backend と同じ形のイベントを積み、`run.progress_events` に入れる。
 * イベントは追記型（番号は 1 から連続、段階のイベントはその段階の今の状態の全体）なので、段階を進めるときは
 * 同じ log に足していく（前のイベントを書き換えない）。
 */

type Json = Record<string, unknown>;
type StepStatus = "pending" | "running" | "done" | "failed" | "skipped";
type TerminalStatus = "done" | "failed" | "cancelled";

export interface ProgressStepOptions {
  /** 段階の種類（省略時は step_id）。`tool:<名前>` は `tool`、`approval_wait` は `approval`。 */
  kind?: string;
  params?: Record<string, string | number | boolean>;
  startedAt?: string;
  finishedAt?: string;
}

/** step_id から種類を決める（backend の記録と同じ規則）。 */
function defaultKind(stepId: string): string | undefined {
  if (stepId.startsWith("tool:")) return "tool";
  if (stepId.startsWith("approval_wait")) return "approval";
  if (stepId.startsWith("respond#")) return "respond";
  return undefined;
}

export class ProgressLog {
  readonly events: Json[] = [];
  private readonly started = new Map<string, string>();
  private readonly params = new Map<string, Record<string, string | number | boolean>>();

  constructor(
    readonly runId: string,
    private readonly clock: () => string = () => new Date().toISOString()
  ) {}

  /** 段階の今の状態を足す。始まった時刻と params は前のイベントから引き継ぐ（backend の記録と同じ）。 */
  step(stepId: string, status: StepStatus, options: ProgressStepOptions = {}): this {
    const now = this.clock();
    const kind = options.kind ?? defaultKind(stepId);
    let startedAt = options.startedAt ?? this.started.get(stepId);
    if (!startedAt && status === "running") startedAt = now;
    if (startedAt) this.started.set(stepId, startedAt);
    const event: Json = {
      schema_version: 1,
      type: "step",
      seq: this.events.length + 1,
      target_id: this.runId,
      attempt: 0,
      emitted_at: now,
      step_id: stepId,
      status,
    };
    if (kind && kind !== stepId) event.kind = kind;
    if (startedAt) event.started_at = startedAt;
    const finishedAt = options.finishedAt ?? (status === "done" || status === "failed" ? now : undefined);
    if (finishedAt) event.finished_at = finishedAt;
    const params = options.params ?? this.params.get(stepId);
    if (params) {
      this.params.set(stepId, params);
      event.params = params;
    }
    this.events.push(event);
    return this;
  }

  /** 終端（完了・失敗・停止）。 */
  terminal(status: TerminalStatus): this {
    this.events.push({
      schema_version: 1,
      type: "terminal",
      seq: this.events.length + 1,
      target_id: this.runId,
      attempt: 0,
      emitted_at: this.clock(),
      status,
    });
    return this;
  }
}

/**
 * spec がイベントを入れていない mock の Run の、状態から作る最小のイベント（考えている → 回答の作成の完了）。
 * 状態が進んでも前の番号のイベントは変わらない（追記型）ので、取り直しのたびに作っても画面の記録と矛盾しない。
 */
export function progressEventsForStatus(run: Json): Json[] {
  const id = String(run.id);
  const createdAt = typeof run.created_at === "string" ? run.created_at : new Date(0).toISOString();
  const endedAt = typeof run.updated_at === "string" ? run.updated_at : createdAt;
  const log = new ProgressLog(id, () => endedAt);
  log.step("plan", "running", { startedAt: createdAt });
  switch (run.status) {
    case "completed":
      return log.step("plan", "done").step("respond", "done").terminal("done").events;
    case "failed":
      return log.step("plan", "failed").terminal("failed").events;
    case "cancelled":
      return log.step("plan", "skipped", { finishedAt: endedAt }).terminal("cancelled").events;
    default:
      return log.events;
  }
}
