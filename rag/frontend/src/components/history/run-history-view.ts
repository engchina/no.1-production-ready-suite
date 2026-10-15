/**
 * 実行履歴（#1890・#1955）の表示の対応表（backend の値 → 共通の部品の値と文言）。画面とテストが同じ規則を使う。
 *
 * - 状態は共通の 7 つ（`@production-ready/ui` の `RunStatus`）に写す（run-history.md「状態の写し方」）。
 *   RAG は `success` → 完了、`blocked`（安全チェック）→ ブロック（文言は「安全チェックで停止」）、`error` → 失敗。
 * - 処理の経過は、段（`stages`）を `RunTimeline` の 1 行ずつにし、回答フローの中の工程（`answer_step:` の段か、
 *   回答の記録の `execution_steps`）を「根拠の検索と回答の生成」の段の下に字下げで並べる。
 */
import {
  formatRunDuration,
  type RunExecutorFilter as SharedRunExecutorFilter,
  type RunStatus as SharedRunStatus,
  type RunStatusFilter as SharedRunStatusFilter,
  type RunStatusLabels,
  type RunTimelineStep,
  type RunTimelineStepStatus,
  type StatusVariant,
} from "@production-ready/ui";

import { outcomeBadge, type AnswerOutcome } from "@/lib/answer-diagnostics";
import { ANSWER_STEP_STAGE_PREFIX, answerStageLabel } from "@/lib/answer-progress";
import type {
  RunDetail,
  RunExecutorFilter,
  RunStatus,
  RunSummary,
  RunSurface,
  RunVerification,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";

export interface BadgeView {
  variant: StatusVariant;
  label: string;
}

/** RAG の実行が取りうる共通の状態（一覧の「状態」の絞り込みの選択肢）。 */
export const RUN_STATUS_OPTIONS = ["succeeded", "blocked", "failed"] as const satisfies readonly SharedRunStatus[];

const TO_SHARED_STATUS: Record<RunStatus, SharedRunStatus> = {
  success: "succeeded",
  blocked: "blocked",
  error: "failed",
};

const FROM_SHARED_STATUS: Partial<Record<SharedRunStatus, RunStatus>> = {
  succeeded: "success",
  blocked: "blocked",
  failed: "error",
};

/** RAG の状態 → 共通の状態。 */
export function toRunStatus(status: RunStatus): SharedRunStatus {
  return TO_SHARED_STATUS[status] ?? "failed";
}

/** 「状態」の絞り込み → API の `status`（すべては付けない）。 */
export function runStatusParam(filter: SharedRunStatusFilter): RunStatus | undefined {
  return filter === "all" ? undefined : FROM_SHARED_STATUS[filter];
}

/** 「実行者」の絞り込み（共通の self / others）→ API の `executor`（RAG は mine / others。#1954）。 */
export function runExecutorParam(filter: SharedRunExecutorFilter): RunExecutorFilter {
  return filter === "self" ? "mine" : filter;
}

/** 状態の文言の RAG の上書き（ブロックは安全チェックで止めた実行）。 */
export function runStatusLabels(): Partial<RunStatusLabels> {
  return {
    succeeded: t("history.status.success"),
    blocked: t("history.status.blocked"),
    failed: t("history.status.error"),
  };
}

/** 種類の絞り込みの値（すべて / チャット / RAG 検索）。 */
export const RUN_SURFACE_FILTERS = ["all", "chat", "search"] as const;
export type RunSurfaceFilter = (typeof RUN_SURFACE_FILTERS)[number];

export function runSurfaceLabel(surface: RunSurface): string {
  return surface === "chat" ? t("history.surface.chat") : t("history.surface.search");
}

/**
 * 回答の対応のバッジ。回答を作らなかった RAG 検索は「回答なし（検索のみ）」、止めた・失敗した実行は
 * 「回答なし」。回答した実行は対応（answered は「回答」、ほかは回答の詳細と同じ文言）。
 */
export function runOutcomeView(run: Pick<RunSummary, "answer_generated" | "status" | "answer_outcome">): BadgeView {
  if (!run.answer_generated) {
    return { variant: "neutral", label: t("history.outcome.searchOnly") };
  }
  if (run.status !== "success" || !run.answer_outcome) {
    return { variant: "neutral", label: t("history.outcome.none") };
  }
  const badge = outcomeBadge(run.answer_outcome as AnswerOutcome);
  if (badge) return { variant: badge.variant as StatusVariant, label: t(badge.key) };
  return { variant: "success", label: t("history.outcome.answered") };
}

/** 回答の検証のバッジ。回答を作らなかった・止めた・失敗した実行は null（「—」）。 */
export function runVerificationView(verification: RunVerification | null): BadgeView | null {
  switch (verification) {
    case "verified":
      return { variant: "success", label: t("history.verification.verified") };
    case "partial":
      return { variant: "warning", label: t("history.verification.partial") };
    case "unverified":
      return { variant: "neutral", label: t("history.verification.unverified") };
    default:
      return null;
  }
}

/** 検索・回答プロファイルの表示名（ID は出さない）。 */
export function runProfileLabel(run: Pick<RunSummary, "search_answer_profile_id" | "search_answer_profile_name">): string {
  if (!run.search_answer_profile_id) return t("history.profile.none");
  return run.search_answer_profile_name || t("history.profile.missing");
}

/** 実行した利用者（実行者）の表示名（引けない利用者は「削除済みのユーザー」。UUID は出さない）。 */
export function runExecutorLabel(run: Pick<RunSummary, "executor_display_name">): string {
  return run.executor_display_name?.trim() || t("history.user.missing");
}

/** 会話の持ち主の表示名。会話の無い実行は null（詳細に出さない）。 */
export function runConversationOwnerLabel(run: { conversation_owner_display_name: string | null }): string | null {
  if (run.conversation_owner_display_name == null) return null;
  return run.conversation_owner_display_name.trim() || t("history.user.missing");
}

/**
 * 詳細の「会話の持ち主」（実行者と違うときだけ。#1954）。会話の無い実行と、自分が実行した自分の会話は null。
 * 見ている利用者から見て、実行者と持ち主のどちらか一方だけが自分なら別の人。どちらも自分でなければ名前で比べる。
 */
export function runConversationOwner(
  run: Pick<RunDetail, "conversation_owner_display_name" | "owned_by_me" | "executed_by_me" | "executor_display_name">
): { name: string; isSelf: boolean } | null {
  const name = runConversationOwnerLabel(run);
  if (name == null) return null;
  const ownedByMe = run.owned_by_me === true;
  if (ownedByMe && run.executed_by_me) return null;
  if (!ownedByMe && !run.executed_by_me && name === runExecutorLabel(run)) return null;
  return { name, isSelf: ownedByMe };
}

/**
 * 開始日時（ISO 8601）。記録の時刻（`created_at`）は実行が終わったときなので、所要時間を引いて開始にする。
 * 所要時間の記録が無ければ記録の時刻のまま。
 */
export function runStartedAt(run: Pick<RunSummary, "created_at" | "elapsed_ms">): string {
  const finished = Date.parse(run.created_at);
  if (run.elapsed_ms == null || !Number.isFinite(run.elapsed_ms) || Number.isNaN(finished)) return run.created_at;
  return new Date(finished - run.elapsed_ms).toISOString();
}

/** 段の結果（success / error / cancelled）・工程の状態 → 処理の経過の状態。止めた段は「停止」（`skipped` の文言を上書き）。 */
export function runStepStatus(outcome: string): RunTimelineStepStatus {
  const value = outcome.toLowerCase();
  if (value.includes("cancel") || value.includes("skip")) return "skipped";
  if (value === "success" || value === "completed" || value === "done" || value === "ok" || value === "") return "done";
  return "failed";
}

/** 処理の経過の文言の RAG の上書き（止めた段は「停止」）。 */
export function runTimelineStepLabels(): Partial<Record<RunTimelineStepStatus, string>> {
  return { skipped: t("history.process.outcome.cancelled") };
}

/** 処理の経過の行（段と、回答フローの中の工程）。 */
export function runTimelineSteps(run: Pick<RunDetail, "stages" | "steps" | "created_at" | "elapsed_ms">): RunTimelineStep[] {
  const llmCalls = new Map(run.steps.map((step) => [step.name, step.llm_calls ?? 0]));
  const detailFor = (name: string) => {
    const calls = llmCalls.get(name) ?? 0;
    return calls > 0 ? t("history.process.llmCalls", { count: formatNumber(calls) }) : undefined;
  };
  const topLevel = run.stages.filter((stage) => !stage.stage.startsWith(ANSWER_STEP_STAGE_PREFIX));
  // 回答フローの中の工程: 進捗の `answer_step:` の段（チャット・流した RAG 検索）か、無ければ回答の記録の工程。
  const nestedStages = run.stages.filter((stage) => stage.stage.startsWith(ANSWER_STEP_STAGE_PREFIX));
  const children =
    nestedStages.length > 0
      ? nestedStages.map((stage) => {
          const name = answerStageLabel(stage.stage);
          return { label: name, status: runStepStatus(stage.outcome), durationMs: stage.elapsed_ms, detail: detailFor(name) };
        })
      : run.steps.map((step) => ({
          label: step.name || t("history.notRecorded"),
          status: runStepStatus(step.status),
          durationMs: step.elapsed_seconds == null ? null : step.elapsed_seconds * 1000,
          detail: detailFor(step.name),
        }));

  // 段は順に並び、時刻は記録していないので所要時間から位置を作る（工程は「根拠の検索と回答の生成」の段の始まりから）。
  const origin = Date.parse(runStartedAt(run));
  const base = Number.isNaN(origin) ? 0 : origin;
  const iso = (ms: number) => new Date(ms).toISOString();
  const rows: RunTimelineStep[] = [];
  let cursor = base;
  const parentIndex = topLevel.findIndex((stage) => stage.stage === "answer" || stage.stage === "retrieval");
  const placeChildren = (start: number) => {
    let childCursor = start;
    children.forEach((child, index) => {
      const duration = child.durationMs;
      const timed = duration != null && Number.isFinite(duration);
      rows.push({
        id: `step-${index}`,
        label: child.label,
        status: child.status,
        durationMs: timed ? duration : null,
        startedAt: timed ? iso(childCursor) : null,
        finishedAt: timed ? iso(childCursor + duration) : null,
        detail: child.detail,
        level: 1,
      });
      if (timed) childCursor += duration;
    });
  };
  topLevel.forEach((stage, index) => {
    const duration = stage.elapsed_ms;
    const timed = duration != null && Number.isFinite(duration);
    rows.push({
      id: `stage-${index}`,
      label: answerStageLabel(stage.stage),
      status: runStepStatus(stage.outcome),
      durationMs: timed ? duration : null,
      startedAt: timed ? iso(cursor) : null,
      finishedAt: timed ? iso(cursor + duration) : null,
    });
    if (index === parentIndex) placeChildren(cursor);
    if (timed) cursor += duration;
  });
  if (parentIndex < 0) placeChildren(base);
  return rows;
}

/** 所要時間（ミリ秒）の表記（共通の書式。「1.2 秒」「2 分 5 秒」、記録なしは「—」）。 */
export function formatRunElapsed(elapsedMs: number | null | undefined): string {
  return formatRunDuration(elapsedMs, t("history.notRecorded"));
}
