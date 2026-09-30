/**
 * 回答生成（RAG 検索・チャット）の進捗の工程の名前と、今の工程の判定（#375）。
 *
 * backend は SSE の `stage` で、会話を踏まえた質問の書き換え（`history_rewrite`）・根拠の検索と
 * 回答の生成（`answer`。中の各工程は `answer_step:` で入れ子に送る）・検索だけのとき（`retrieval`）を送る。
 * 工程の名前は backend の時間切れの文言（`app/rag/answer_timeout.py` の `ANSWER_STAGE_LABELS`）と揃える。
 */

import { t, type I18nKey } from "./i18n";

export interface AnswerStageEvent {
  stage: string;
  outcome: "started" | "success" | "error" | "cancelled";
}

export const ANSWER_STAGE_LABEL: Record<string, I18nKey> = {
  retrieval: "search.stage.retrieval",
  history_rewrite: "search.stage.historyRewrite",
  answer: "search.stage.answer",
};

/**
 * 回答フローの中の各工程（質問の理解・文書検索など）の工程名の接頭辞（#593）。後ろは利用者向けの
 * 工程名（日本語）で、そのまま表示する。backend の `app/rag/answer_engine.py` の
 * `ANSWER_STEP_STAGE_PREFIX` と同じ。
 */
export const ANSWER_STEP_STAGE_PREFIX = "answer_step:";

/** 工程の表示名。未知の工程は「処理」。 */
export function answerStageLabel(stage: string): string {
  if (stage.startsWith(ANSWER_STEP_STAGE_PREFIX)) {
    const name = stage.slice(ANSWER_STEP_STAGE_PREFIX.length).trim();
    if (name) return name;
  }
  return t(ANSWER_STAGE_LABEL[stage] ?? "search.stage.processing");
}

/**
 * 今の工程（最後に始まった、まだ終わっていない工程）。工程は入れ子になる（追加の検索の中の根拠の
 * 整理など）ため、終わった工程を外して残りの最後を返す。どれも実行中でなければ最後に通知された工程。
 * backend の `StageTracker` と同じ判定。
 */
export function currentAnswerStage(events: readonly AnswerStageEvent[]): string | null {
  const active: string[] = [];
  let last: string | null = null;
  for (const event of events) {
    last = event.stage;
    if (event.outcome === "started") {
      active.push(event.stage);
      continue;
    }
    const index = active.lastIndexOf(event.stage);
    if (index >= 0) active.splice(index, 1);
  }
  return active.length ? active[active.length - 1] : last;
}

/**
 * 回答生成中の表示の文言（「回答を生成しています（今の工程）」）。回答を作らない RAG 検索は
 * 「検索しています（今の工程）」（#649）。
 */
export function answerProgressLabel(
  events: readonly AnswerStageEvent[],
  { generateAnswer = true }: { generateAnswer?: boolean } = {}
): string {
  const stage = currentAnswerStage(events);
  return t(generateAnswer ? "answer.progress.label" : "answer.progress.searchLabel", {
    stage: stage ? answerStageLabel(stage) : t("answer.progress.preparing"),
  });
}
