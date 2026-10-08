/**
 * チャットの回答の処理の段階（3 製品共通の `ChatProgressStep`。#1145 / #1146）。
 *
 * backend はチャットの SSE の `progress` で、段階の一覧（`app/rag/chat_progress.py`。質問の整理 → 文書の検索 →
 * 並べ替え → 回答の作成 → 回答の確認）を段階が変わるたびに全体で送る。画面は段階の id と状態から、
 * 状態に合わせた名前（実行中「〜しています」・完了「〜しました」・失敗「〜できませんでした」・未実行は名詞。
 * NL2SQL・Agent と同じ）を i18n で付ける。未知の段階は backend の名前をそのまま出す。
 */

import type { ChatProgressStep, ChatProgressStepStatus } from "@production-ready/ui";

import { t } from "./i18n";

const STATUSES: readonly ChatProgressStepStatus[] = ["pending", "running", "done", "failed", "skipped"];

type LabelState = "running" | "done" | "failed" | "idle";

/** 段階の名前。i18n の key は静的に書く（辞書の検査が key を見つけられるように）。 */
const STEP_LABELS: Record<string, () => Record<LabelState, string>> = {
  rewrite_query: () => ({
    running: t("chat.progress.rewrite_query.running"),
    done: t("chat.progress.rewrite_query.done"),
    failed: t("chat.progress.rewrite_query.failed"),
    idle: t("chat.progress.rewrite_query.idle"),
  }),
  retrieve: () => ({
    running: t("chat.progress.retrieve.running"),
    done: t("chat.progress.retrieve.done"),
    failed: t("chat.progress.retrieve.failed"),
    idle: t("chat.progress.retrieve.idle"),
  }),
  rerank: () => ({
    running: t("chat.progress.rerank.running"),
    done: t("chat.progress.rerank.done"),
    failed: t("chat.progress.rerank.failed"),
    idle: t("chat.progress.rerank.idle"),
  }),
  generate_answer: () => ({
    running: t("chat.progress.generate_answer.running"),
    done: t("chat.progress.generate_answer.done"),
    failed: t("chat.progress.generate_answer.failed"),
    idle: t("chat.progress.generate_answer.idle"),
  }),
  check_guardrail: () => ({
    running: t("chat.progress.check_guardrail.running"),
    done: t("chat.progress.check_guardrail.done"),
    failed: t("chat.progress.check_guardrail.failed"),
    idle: t("chat.progress.check_guardrail.idle"),
  }),
};

function labelState(status: ChatProgressStepStatus): LabelState {
  return status === "running" || status === "done" || status === "failed" ? status : "idle";
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value ? value : undefined;
}

/**
 * SSE の `progress` の `steps` を段階の一覧にする。形の違う要素は捨てる（SSE の data は未検証の入力）。
 */
export function chatProgressStepsFromEvent(value: unknown): ChatProgressStep[] {
  if (!Array.isArray(value)) return [];
  const steps: ChatProgressStep[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const id = optionalString(record.id);
    const status = STATUSES.find((candidate) => candidate === record.status);
    if (!id || !status) continue;
    const labels = STEP_LABELS[id]?.();
    steps.push({
      id,
      label: labels ? labels[labelState(status)] : (optionalString(record.label) ?? id),
      status,
      startedAt: optionalString(record.startedAt),
      finishedAt: optionalString(record.finishedAt),
      detail: optionalString(record.detail),
    });
  }
  return steps;
}

/**
 * 送った質問（サーバーの `start` の前）の段階。送信が遅いとき、回答の作成ではなく送信で待っていることが分かる。
 * `start` の後、最初の `progress` が届くまでも同じ段階を出す。
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
