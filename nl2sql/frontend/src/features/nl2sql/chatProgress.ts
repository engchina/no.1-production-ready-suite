// チャットのジョブの処理の段階の定義（#1145 / #1359）。
//
// 段階は backend がジョブの処理の段階のイベント（3 製品共通の契約。`progress_events`）として記録する（開始待ち・
// 準備・生成・安全性の確認・実行・結果の整形。並びは backend の記録の順）。画面はイベントを共有の
// `useChatProgressStream` で積み、ここで段階の名前（i18n）と補足（生成方法・参照した表・行数・停止）を付けるだけ
// にする。段階の状態（開始前の失敗・停止・終端の未実行・終端まで今の段階がある〔#1176〕）は backend が決める。
import {
  chatProgressLabelState,
  type ChatProgressLabels,
  type ChatProgressLabelState,
  type ChatProgressParams,
  type ChatProgressStep,
  type ChatProgressStepDefinitions,
  type ChatProgressStepStatus,
} from "@production-ready/ui";

// node:test(jiti)から直接 import されるため、"@/" alias でなく相対 path を使う。
import { t } from "../../lib/i18n";
import type { JobData, Nl2SqlEngine } from "./types";

type ChatStage =
  | "queue"
  | "prepare_context"
  | "generate_sql"
  | "safety_check"
  | "execute_sql"
  | "format_results";

/** 段階の名前（実行中・完了・失敗・未実行）。i18n の key は静的に書く（辞書の検査が key を見つけられるように）。 */
const STAGE_LABELS: Record<ChatStage, Record<ChatProgressLabelState, string>> = {
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
  execute_sql: {
    running: t("chat.progress.execute_sql.running"),
    done: t("chat.progress.execute_sql.done"),
    failed: t("chat.progress.execute_sql.failed"),
    idle: t("chat.progress.execute_sql.idle"),
  },
  format_results: {
    running: t("chat.progress.format_results.running"),
    done: t("chat.progress.format_results.done"),
    failed: t("chat.progress.format_results.failed"),
    idle: t("chat.progress.format_results.idle"),
  },
};

const ENGINE_LABELS: Record<Nl2SqlEngine, string> = {
  select_ai: t("chat.engine.selectAi"),
  select_ai_agent: t("chat.engine.selectAiAgent"),
  enterprise_ai_direct: t("chat.engine.enterprise"),
};

/** 共通の部品の文言のうち、NL2SQL の語にそろえるもの（スキップは SQL 生成の画面と同じ「未実行」）。 */
export const CHAT_PROGRESS_LABELS: Partial<ChatProgressLabels> = {
  working: t("chat.progress.working"),
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

function label(stage: ChatStage) {
  return (status: ChatProgressStepStatus) => STAGE_LABELS[stage][chatProgressLabelState(status)];
}

function count(value: ChatProgressParams[string] | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** 止めた段階（backend の停止の印）は「停止しました」。停止は失敗ではないので、状態は「未実行」。 */
function stoppedDetail(params: ChatProgressParams): string | undefined {
  return params.stopped === true ? t("chat.progress.stopped") : undefined;
}

/** 生成方法の名前（未知の値は出さない）。 */
function engineDetail(params: ChatProgressParams): string | undefined {
  const engine = params.engine;
  return typeof engine === "string" && Object.prototype.hasOwnProperty.call(ENGINE_LABELS, engine)
    ? ENGINE_LABELS[engine as Nl2SqlEngine]
    : undefined;
}

/** 参照した表を短く（backend が渡す最初の数件と「ほか N 件」）。 */
function tablesDetail(params: ChatProgressParams): string | undefined {
  const tables = typeof params.tables === "string" ? params.tables : "";
  if (!tables) return undefined;
  const shown = tables.split(",").filter((name) => name.trim()).length;
  const total = count(params.table_count) ?? shown;
  return total > shown ? t("chat.progress.tablesMore", { tables, count: total - shown }) : tables;
}

/** 実行で取得した行数。 */
function rowsDetail(params: ChatProgressParams): string | undefined {
  const rows = count(params.rows);
  return rows === null ? undefined : t("chat.progress.rows", { count: rows.toLocaleString("ja-JP") });
}

/**
 * チャットのジョブの段階の定義（`kind` = backend の段階の id → 名前と補足）。並びは backend の記録の順。
 *
 * - 開始待ち（ジョブの作成から worker が処理を始めるまで）: worker が起動していない・混んでいるときに、生成では
 *   なく開始を待っていることが分かる。
 * - 生成の段階の補足は生成方法、安全性の確認（完了）は参照した表、実行（完了）は取得した行数。
 * - 止めた段階は「停止しました」（ほかの補足より先）。
 */
export const CHAT_PROGRESS_STEP_DEFINITIONS: ChatProgressStepDefinitions = {
  queue: { label: label("queue"), detail: (_status, params) => stoppedDetail(params) },
  prepare_context: {
    label: label("prepare_context"),
    detail: (_status, params) => stoppedDetail(params),
  },
  generate_sql: {
    label: label("generate_sql"),
    detail: (_status, params) => stoppedDetail(params) ?? engineDetail(params),
  },
  safety_check: {
    label: label("safety_check"),
    detail: (status, params) =>
      stoppedDetail(params) ?? (status === "done" ? tablesDetail(params) : undefined),
  },
  execute_sql: {
    label: label("execute_sql"),
    detail: (status, params) =>
      stoppedDetail(params) ?? (status === "done" ? rowsDetail(params) : undefined),
  },
  format_results: {
    label: label("format_results"),
    detail: (_status, params) => stoppedDetail(params),
  },
};

function isoFromMs(ms: number): string {
  return new Date(ms).toISOString();
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

/** 完了後の全体の所要時間（ジョブの作成から終了まで）。段階の時刻が無い古いジョブにも出す。 */
export function chatJobElapsedMs(job: JobData): number | null {
  if (job.status !== "done" && job.status !== "error") return null;
  const start = Date.parse(job.created_at);
  const end = job.finished_at ? Date.parse(job.finished_at) : Number.NaN;
  if (Number.isFinite(start) && Number.isFinite(end)) return Math.max(0, end - start);
  return job.elapsed_ms ?? null;
}
