/**
 * 回答（チャット・RAG 検索）の「処理の経過」の工程の行（#2096）。
 *
 * 3 製品共通の `ChatProgress` に渡す 1 工程 = 1 行を、次の 2 つから同じ規則で作る。
 *
 * - チャット: backend の段階のイベント（`app/rag/chat_progress.py`。種類 `answer_step` の値に工程名 `name` と
 *   入れ子の深さ `depth`、回答ができたら LLM の回数 `llm_calls` と結果の説明 `note`）。`lib/chat-progress.ts` の
 *   段階の定義がここの関数で名前・計測値・字下げを付ける。
 * - RAG 検索: SSE の工程のイベント（`stage`。`answer_step:<工程名>` と回答フローの外の工程）。入れ子の深さは
 *   開始と終了の順から決め、完了の後は回答の記録（`execution_steps`）の LLM の回数・結果の説明を足す。
 *
 * 全体を包む工程（`answer` / `retrieval`）は、中の工程を行にするので出さない。工程の名前は
 * `answerStepDisplayName` で利用者向けの名前にする。
 */
import type { ChatProgressStep, ChatProgressStepStatus } from "@production-ready/ui";

import type { AnswerDiagnostics } from "./answer-diagnostics";
import { t, type I18nKey } from "./i18n";

/**
 * 回答フローの中の各工程（質問の理解・文書検索など）の工程名の接頭辞（#593）。後ろは backend の工程名（日本語）で、
 * 画面は `answerStepDisplayName` で利用者向けの名前にする。backend の `app/rag/answer_engine.py` の
 * `ANSWER_STEP_STAGE_PREFIX` と同じ。
 */
export const ANSWER_STEP_STAGE_PREFIX = "answer_step:";

/** 回答フローの外の工程のうち、行にするもの（工程の名前 → 名前の i18n）。 */
export const PIPELINE_STEP_LABEL: Readonly<Record<string, I18nKey>> = {
  history_rewrite: "search.stage.historyRewrite",
  field_filter: "search.stage.fieldFilter",
  answer_guardrail: "search.stage.answerGuardrail",
};

/** 回答フローの工程の段階の種類（backend の `ANSWER_STEP_KIND`）。 */
export const ANSWER_STEP_KIND = "answer_step";

/** 回答の記録の工程の状態のうち、結果の説明ではないもの（完了・例外で中断・中止。失敗は状態で示す）。 */
const PLAIN_STEP_STATUSES = new Set(["", "complete", "エラーで中断", "中止"]);
/** 回答の記録の工程の状態のうち、工程の失敗（#2056 と同じ）。 */
const FAILED_STEP_STATUSES = new Set(["エラーで中断", "中止"]);

/**
 * backend の工程名（`rag_engine.generation.answering` の `_execution_step`）→ 利用者向けの名前（i18n）。
 * backend の工程名はログ・記録の識別に使うので変えず、画面の名前だけをここで言い換える（#2096）。
 * 回数つきの工程（「文書検索（2回目）」）は回数を除いた名前で引き、「〈名前〉（2 回目）」にする。
 */
const ANSWER_STEP_NAME: Readonly<Record<string, I18nKey>> = {
  質問の理解: "answer.step.understandQuestion",
  "用語・ルールの確認": "answer.step.glossary",
  検索の準備確認: "answer.step.checkSearchReady",
  質問拡張戦略: "answer.step.expandQuestion",
  検索文と検索語の確定: "answer.step.decideQueries",
  回答生成フロー: "answer.step.answerFlow",
  "検索語の再確定（書き換え後）": "answer.step.redecideKeywords",
  文書検索: "answer.step.searchDocuments",
  Rerank: "answer.step.rerank",
  文書の選択: "answer.step.selectDocuments",
  文書の選択の解除: "answer.step.unselectDocuments",
  根拠確認: "answer.step.checkEvidence",
  根拠確認の結論: "answer.step.evidenceConclusion",
  補正検索の準備: "answer.step.prepareResearch",
  文脈の追加: "answer.step.addContext",
  画面の選択: "answer.step.selectScreens",
  章節の拡張: "answer.step.expandSections",
  要求ごとの根拠の確認: "answer.step.coverRequests",
  回答に使う画像の確認: "answer.step.checkImages",
  回答文の生成と根拠確認: "answer.step.generateAnswer",
  是正: "answer.step.revise",
  是正の打ち切り: "answer.step.stopRevising",
};

const STEP_ATTEMPT = /（(\d+)回目）$/;

/** 工程の利用者向けの名前（backend の工程名から。知らない工程名はそのまま）。 */
export function answerStepDisplayName(name: string): string {
  const value = name.trim();
  if (!value) return t("search.stage.processing");
  if (Object.prototype.hasOwnProperty.call(ANSWER_STEP_NAME, value)) return t(ANSWER_STEP_NAME[value]);
  const attempt = STEP_ATTEMPT.exec(value);
  const base = attempt ? value.slice(0, attempt.index) : value;
  if (!attempt || !Object.prototype.hasOwnProperty.call(ANSWER_STEP_NAME, base)) return value;
  return t("answer.step.attempt", { name: t(ANSWER_STEP_NAME[base]), count: Number(attempt[1]) });
}

/** 右の列の計測値（結果の説明・LLM の回数）。無ければ undefined。 */
export function answerStepMeta({ llmCalls, note }: { llmCalls?: number | null; note?: string | null }): string | undefined {
  const parts = [
    note && !PLAIN_STEP_STATUSES.has(note.trim()) ? note.trim() : null,
    llmCalls && llmCalls > 0 ? t("search.answerDetails.llmCalls", { count: llmCalls }) : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : undefined;
}

/** 工程のイベントの種類（行にしない工程は null）。 */
function stageKind(stage: string): string | null {
  if (stage.startsWith(ANSWER_STEP_STAGE_PREFIX)) {
    return stage.slice(ANSWER_STEP_STAGE_PREFIX.length).trim() ? ANSWER_STEP_KIND : null;
  }
  return Object.prototype.hasOwnProperty.call(PIPELINE_STEP_LABEL, stage) ? stage : null;
}

/** 工程の名前（行の名前）。 */
function stageLabel(stage: string, kind: string): string {
  if (kind === ANSWER_STEP_KIND) return answerStepDisplayName(stage.slice(ANSWER_STEP_STAGE_PREFIX.length));
  return t(PIPELINE_STEP_LABEL[kind]);
}

/** SSE の工程のイベント（RAG 検索。受け取った時刻を足したもの）。 */
export interface AnswerStageRecord {
  stage: string;
  outcome: "started" | "success" | "error" | "cancelled";
  elapsed_ms: number;
  /** 画面が受け取った時刻（epoch ms。実行中の工程の経過時間の起点）。 */
  receivedAtMs?: number;
}

/** 終わり方（実行中の工程を何にするか）。null は処理中。 */
export type AnswerProcessTerminal = "done" | "failed" | "cancelled" | null;

const TERMINAL_RUNNING_STATUS: Record<Exclude<AnswerProcessTerminal, null>, ChatProgressStepStatus> = {
  done: "done",
  failed: "failed",
  cancelled: "skipped",
};

interface StageRow extends ChatProgressStep {
  /** 回答の記録と対応させる工程名（回答フローの工程だけ）。 */
  stepName?: string;
}

/**
 * RAG 検索の工程のイベントから、処理の経過の行を作る。終端（`terminal`）の後は実行中の工程を終わらせ、
 * 回答の記録の工程（`executionSteps`）があれば名前の順に対応させて計測値・失敗を足す。
 */
export function answerProcessStepsFromStages(
  stages: readonly AnswerStageRecord[],
  {
    terminal = null,
    executionSteps = [],
  }: { terminal?: AnswerProcessTerminal; executionSteps?: AnswerDiagnostics["steps"] } = {}
): ChatProgressStep[] {
  const rows: StageRow[] = [];
  const open: { stage: string; index: number }[] = [];
  for (const event of stages) {
    const kind = stageKind(event.stage);
    if (kind === null) continue;
    if (event.outcome === "started") {
      const depth = kind === ANSWER_STEP_KIND ? open.filter((item) => stageKind(item.stage) === ANSWER_STEP_KIND).length : 0;
      const row: StageRow = { id: `s${rows.length + 1}`, label: stageLabel(event.stage, kind), status: "running" };
      if (event.receivedAtMs !== undefined) row.startedAt = new Date(event.receivedAtMs).toISOString();
      if (depth > 0) row.level = depth;
      if (kind === ANSWER_STEP_KIND) row.stepName = event.stage.slice(ANSWER_STEP_STAGE_PREFIX.length).trim();
      open.push({ stage: event.stage, index: rows.length });
      rows.push(row);
      continue;
    }
    let position = -1;
    for (let index = open.length - 1; index >= 0; index -= 1) {
      if (open[index].stage === event.stage) {
        position = index;
        break;
      }
    }
    if (position < 0) continue;
    const [{ index }] = open.splice(position, 1);
    const row = rows[index];
    if (event.outcome === "cancelled") {
      // 中断（停止・時間切れ）は終端で決める。
      continue;
    }
    row.status = event.outcome === "success" ? "done" : "failed";
    if (Number.isFinite(event.elapsed_ms)) row.durationMs = Math.max(0, event.elapsed_ms);
  }
  if (terminal !== null) {
    for (const row of rows) {
      if (row.status === "running" || row.status === "pending") row.status = TERMINAL_RUNNING_STATUS[terminal];
    }
  }
  // 回答の記録の工程を名前の順に対応させる（同じ名前の 2 回目は次の記録）。
  let cursor = 0;
  for (const row of rows) {
    if (row.stepName === undefined) continue;
    let match = -1;
    for (let index = cursor; index < executionSteps.length; index += 1) {
      if (executionSteps[index].name === row.stepName) {
        match = index;
        break;
      }
    }
    if (match < 0) continue;
    cursor = match + 1;
    const record = executionSteps[match];
    const meta = answerStepMeta({ llmCalls: record.llmCalls, note: record.status });
    if (meta) row.meta = meta;
    if (FAILED_STEP_STATUSES.has(record.status.trim())) row.status = "failed";
  }
  return rows.map((row) => {
    const step: ChatProgressStep = { id: row.id, label: row.label, status: row.status };
    if (row.startedAt) step.startedAt = row.startedAt;
    if (row.durationMs != null) step.durationMs = row.durationMs;
    if (row.meta) step.meta = row.meta;
    if (row.level) step.level = row.level;
    return step;
  });
}
