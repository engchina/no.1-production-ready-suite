/**
 * 回答生成（RAG 検索・チャット）の進捗の工程の名前と、今の工程の判定（#375）。
 *
 * backend は embedding / retrieval / rerank / generation に加え、LLM を呼ぶ検索の計画
 * （`agentic_planning`）・追加の検索の計画（`agentic_multi_hop`）などを SSE の `stage` で送る。
 * 工程の名前は backend の時間切れの文言（`app/rag/answer_timeout.py` の `ANSWER_STAGE_LABELS`）と揃える。
 */

import { t, type I18nKey } from "./i18n";

export interface AnswerStageEvent {
  stage: string;
  outcome: "started" | "success" | "error" | "cancelled";
}

export const ANSWER_STAGE_LABEL: Record<string, I18nKey> = {
  query_expansion: "search.stage.queryExpansion",
  agentic_planning: "search.stage.agentic",
  embedding: "search.stage.embedding",
  retrieval: "search.stage.retrieval",
  rerank: "search.stage.rerank",
  business_fit_weighting: "search.stage.businessFit",
  context_adaptive_expansion: "search.stage.context",
  context_compression: "search.stage.context",
  context_dependency_promotion: "search.stage.context",
  context_diversity: "search.stage.context",
  context_expansion: "search.stage.context",
  context_group_expansion: "search.stage.context",
  crag_rewrite: "search.stage.queryRewrite",
  crag_retrieval: "search.stage.additionalSearch",
  crag_corrective: "search.stage.additionalSearch",
  corrective_retrieval: "search.stage.corrective",
  agentic_multi_hop: "search.stage.multiHop",
  agentic_multi_hop_retrieval: "search.stage.additionalSearch",
  docrag_history_rewrite: "search.stage.historyRewrite",
  docrag_answer: "search.stage.docragAnswer",
  generation: "search.stage.generation",
  answer_guardrail: "search.stage.answerGuardrail",
};

/** 工程の表示名。未知の工程は「処理」。 */
export function answerStageLabel(stage: string): string {
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

/** 回答生成中の表示の文言（「回答を生成しています（今の工程）」）。 */
export function answerProgressLabel(events: readonly AnswerStageEvent[]): string {
  const stage = currentAnswerStage(events);
  return t("answer.progress.label", {
    stage: stage ? answerStageLabel(stage) : t("answer.progress.preparing"),
  });
}
