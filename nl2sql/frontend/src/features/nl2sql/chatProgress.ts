// チャットのジョブの段階を、3 製品共通の段階の形（ChatProgressStep）にする（#1145）。
//
// SQL 生成の画面の工程の表示（WorkflowProgressStrip）より情報を絞り、チャットが送るだけで実行しない
// （generation_only）ジョブの段階（開始待ち・準備・生成・安全性の確認）だけを出す。
import type {
  ChatProgressLabels,
  ChatProgressStep,
  ChatProgressStepStatus,
} from "@engchina/production-ready-ui";

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import { normalizeNl2SqlJobSteps } from "./jobProgressState";
import type { JobData, JobStepStatus, Nl2SqlEngine } from "./types";

type ChatStage = "prepare_context" | "generate_sql" | "safety_check";

/** チャットで出す工程（実行と整形はチャットでは行わない）。 */
const CHAT_STAGES: readonly ChatStage[] = ["prepare_context", "generate_sql", "safety_check"];

/** 段階の名前（実行中・完了・失敗・未実行）。i18n の key は静的に書く（辞書の検査が key を見つけられるように）。 */
const STAGE_LABELS: Record<ChatStage | "queue", Record<"running" | "done" | "failed" | "idle", string>> = {
  queue: {
    running: t("chat.progress.queue.running"),
    done: t("chat.progress.queue.done"),
    failed: t("chat.progress.queue.failed"),
    idle: t("chat.progress.queue.idle"),
  },
  prepare_context: {
    running: t("chat.progress.prepare_context.running"),
    done: t("chat.progress.prepare_context.done"),
    failed: t("chat.progress.prepare_context.failed"),
    idle: t("chat.progress.prepare_context.idle"),
  },
  generate_sql: {
    running: t("chat.progress.generate_sql.running"),
    done: t("chat.progress.generate_sql.done"),
    failed: t("chat.progress.generate_sql.failed"),
    idle: t("chat.progress.generate_sql.idle"),
  },
  safety_check: {
    running: t("chat.progress.safety_check.running"),
    done: t("chat.progress.safety_check.done"),
    failed: t("chat.progress.safety_check.failed"),
    idle: t("chat.progress.safety_check.idle"),
  },
};

const ENGINE_LABELS: Record<Nl2SqlEngine, string> = {
  select_ai: t("chat.engine.selectAi"),
  select_ai_agent: t("chat.engine.selectAiAgent"),
  enterprise_ai_direct: t("chat.engine.enterprise"),
};

/** 共通の部品の文言のうち、NL2SQL の語にそろえるもの（スキップは SQL 生成の画面と同じ「未実行」）。 */
export const CHAT_PROGRESS_LABELS: Partial<ChatProgressLabels> = {
  completedSteps: (count) => t("chat.progress.completed", { count }),
  summary: (count, duration) => t("chat.progress.summary", { count, duration }),
  steps: t("chat.progress.steps"),
  status: {
    pending: t("nl2sql.progress.step.pending"),
    running: t("nl2sql.progress.step.running"),
    done: t("nl2sql.progress.step.done"),
    failed: t("chat.progress.status.failed"),
    skipped: t("nl2sql.progress.step.skipped"),
  },
};

function labelFor(stage: ChatStage | "queue", status: ChatProgressStepStatus): string {
  const labels = STAGE_LABELS[stage];
  if (status === "running") return labels.running;
  if (status === "done") return labels.done;
  if (status === "failed") return labels.failed;
  return labels.idle;
}

function toChatStatus(status: JobStepStatus): ChatProgressStepStatus {
  return status === "error" ? "failed" : status;
}

function isoFromMs(ms: number): string {
  return new Date(ms).toISOString();
}

/** 参照した表を短く（最初の 3 件と「ほか N 件」）。 */
function tablesDetail(tables: string[] | undefined): string | undefined {
  const names = (tables ?? []).filter(Boolean);
  if (names.length === 0) return undefined;
  const shown = names.slice(0, 3).join(", ");
  return names.length > 3
    ? t("chat.progress.tablesMore", { tables: shown, count: names.length - 3 })
    : shown;
}

/** 送信の応答（ジョブの投入）を待つ間の段階。投入が遅いときに、どこで待っているかを示す。 */
export function chatSubmitProgressSteps(sentAtMs: number): ChatProgressStep[] {
  return [
    {
      id: "submit",
      label: t("chat.progress.submit.running"),
      status: "running",
      startedAt: isoFromMs(sentAtMs),
    },
  ];
}

/**
 * チャットのジョブを段階の一覧にする。
 *
 * - 先頭に「開始待ち」（ジョブの作成から worker が処理を始めるまで）を置く。worker が起動していない・
 *   混んでいるときに、生成ではなく開始を待っていることが分かる。
 * - ジョブが終わった（完了・失敗・停止）のに待機中のまま残った段階は「未実行」にする。
 * - 停止（JOB_CANCELLED）は失敗ではないので、止めた段階は「未実行」と補足「停止しました」にする。
 */
export function chatJobProgressSteps(job: JobData, engine?: Nl2SqlEngine): ChatProgressStep[] {
  const terminal = job.status === "done" || job.status === "error";
  const cancelled = job.error_code === "JOB_CANCELLED";
  const reported = new Map((job.steps ?? []).map((step) => [step.stage, step]));
  const normalized = new Map(normalizeNl2SqlJobSteps(job).map((step) => [step.stage, step]));

  const queueStatus: ChatProgressStepStatus =
    job.status === "pending"
      ? "running"
      : job.started_at || job.status !== "error"
        ? "done"
        : cancelled
          ? "skipped"
          : "failed";
  const firstStageStartedAt = reported.get("prepare_context")?.started_at ?? undefined;
  const steps: ChatProgressStep[] = [
    {
      id: "queue",
      label: labelFor("queue", queueStatus),
      status: queueStatus,
      startedAt: job.created_at,
      finishedAt:
        queueStatus === "running" ? undefined : (job.started_at ?? firstStageStartedAt ?? undefined),
      detail: queueStatus === "skipped" ? t("chat.progress.stopped") : undefined,
    },
  ];

  for (const stage of CHAT_STAGES) {
    const step = normalized.get(stage);
    let status = toChatStatus(step?.status ?? "pending");
    // ジョブが待機中の間は、段階は開始待ちの後ろで待つ。
    if (job.status === "pending") status = "pending";
    let detail: string | undefined;
    if (terminal && (status === "pending" || status === "running")) status = "skipped";
    if (cancelled && status === "failed") {
      status = "skipped";
      detail = t("chat.progress.stopped");
    }
    if (!detail && stage === "generate_sql" && engine) detail = ENGINE_LABELS[engine];
    if (!detail && stage === "safety_check" && status === "done")
      detail = tablesDetail(job.result?.safety.referenced_tables);
    const timing = reported.get(stage);
    steps.push({
      id: stage,
      label: labelFor(stage, status),
      status,
      startedAt: timing?.started_at ?? undefined,
      finishedAt: status === "running" ? undefined : (timing?.finished_at ?? undefined),
      detail,
    });
  }
  return steps;
}

/** 完了後の全体の所要時間（ジョブの作成から終了まで）。段階の時刻が無い古いジョブにも出す。 */
export function chatJobElapsedMs(job: JobData): number | null {
  if (job.status !== "done" && job.status !== "error") return null;
  const start = Date.parse(job.created_at);
  const end = job.finished_at ? Date.parse(job.finished_at) : Number.NaN;
  if (Number.isFinite(start) && Number.isFinite(end)) return Math.max(0, end - start);
  return job.elapsed_ms ?? null;
}
