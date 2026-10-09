/**
 * チャットの回答の処理の段階の定義（3 製品共通の段階のイベント。#1146 / #1359）。
 *
 * backend（`app/rag/chat_progress.py`）は段階のイベント（質問の整理 → 文書の検索 → 並べ替え → 回答の作成 →
 * 回答の確認）を記録し、回答の配信（SSE の `chat_progress`）・保存済みの回答（`ChatMessage.progress`）・
 * 段階の polling / SSE（`GET .../messages/{回答の id}/progress[/stream]`）で配る。組み立て・配信の受け取りは
 * 共通の `@production-ready/ui`（`useChatProgressStream` / `chatProgressStepsFromEvents`）が持ち、RAG は段階の
 * 名前と補足（i18n）だけを持つ。名前は状態に合わせる（実行中「〜しています」・完了「〜しました」・失敗
 * 「〜できませんでした」・未実行は名詞。NL2SQL・Agent と同じ）。
 */

import {
  chatProgressLabelState,
  type ChatProgressLabelState,
  type ChatProgressParams,
  type ChatProgressStep,
  type ChatProgressStepDefinitions,
  type ChatProgressStepStatus,
} from "@production-ready/ui";

import { t } from "./i18n";

/** 段階の名前を状態から選ぶ（i18n の key は静的に書く。辞書の検査が key を見つけられるように）。 */
function byState(labels: () => Record<ChatProgressLabelState, string>) {
  return (status: ChatProgressStepStatus) => labels()[chatProgressLabelState(status)];
}

function count(params: ChatProgressParams, key: string): number | undefined {
  const value = params[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** 文書の検索の補足: 完了した検索は根拠の件数、補正検索（2 回目以降）は回数。 */
function retrieveDetail(status: ChatProgressStepStatus, params: ChatProgressParams): string | undefined {
  const citations = count(params, "citations");
  if (status === "done" && citations !== undefined) return t("chat.progress.retrieve.citations", { count: citations });
  const attempt = count(params, "attempt");
  return attempt !== undefined && attempt >= 2 ? t("chat.progress.retrieve.attempt", { count: attempt }) : undefined;
}

/** RAG の段階の定義（段階の id → 名前・補足）。並びは backend の記録の順。 */
export const CHAT_PROGRESS_DEFINITIONS: ChatProgressStepDefinitions = {
  rewrite_query: {
    label: byState(() => ({
      running: t("chat.progress.rewrite_query.running"),
      done: t("chat.progress.rewrite_query.done"),
      failed: t("chat.progress.rewrite_query.failed"),
      idle: t("chat.progress.rewrite_query.idle"),
    })),
  },
  retrieve: {
    label: byState(() => ({
      running: t("chat.progress.retrieve.running"),
      done: t("chat.progress.retrieve.done"),
      failed: t("chat.progress.retrieve.failed"),
      idle: t("chat.progress.retrieve.idle"),
    })),
    detail: retrieveDetail,
  },
  rerank: {
    label: byState(() => ({
      running: t("chat.progress.rerank.running"),
      done: t("chat.progress.rerank.done"),
      failed: t("chat.progress.rerank.failed"),
      idle: t("chat.progress.rerank.idle"),
    })),
  },
  generate_answer: {
    label: byState(() => ({
      running: t("chat.progress.generate_answer.running"),
      done: t("chat.progress.generate_answer.done"),
      failed: t("chat.progress.generate_answer.failed"),
      idle: t("chat.progress.generate_answer.idle"),
    })),
  },
  check_guardrail: {
    label: byState(() => ({
      running: t("chat.progress.check_guardrail.running"),
      done: t("chat.progress.check_guardrail.done"),
      failed: t("chat.progress.check_guardrail.failed"),
      idle: t("chat.progress.check_guardrail.idle"),
    })),
  },
};

/**
 * 送った質問（サーバーの `start` の前）の段階。送信が遅いとき、回答の作成ではなく送信で待っていることが分かる。
 * `start` の後、最初の段階のイベントが届くまでも同じ段階を出す。
 */
export function chatSubmitProgressSteps(sentAtMs: number): ChatProgressStep[] {
  return [
    {
      id: "submit",
      label: t("chat.progress.submit.running"),
      status: "running",
      startedAt: new Date(sentAtMs).toISOString(),
    },
  ];
}
