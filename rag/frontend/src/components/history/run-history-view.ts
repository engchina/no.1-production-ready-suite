/**
 * 実行履歴（#1890）の表示の対応表（backend の値 → バッジの variant と文言）。画面とテストが同じ規則を使う。
 */
import type { StatusVariant } from "@production-ready/ui";

import { outcomeBadge, type AnswerOutcome } from "@/lib/answer-diagnostics";
import type { RunExecutorFilter, RunStatus, RunSummary, RunSurface, RunVerification } from "@/lib/api";
import { t } from "@/lib/i18n";

export interface BadgeView {
  variant: StatusVariant;
  label: string;
}

export const RUN_STATUS_FILTERS = ["all", "success", "blocked", "error"] as const;
export type RunStatusFilter = (typeof RUN_STATUS_FILTERS)[number];

export const RUN_SURFACE_FILTERS = ["all", "chat", "search"] as const;
export type RunSurfaceFilter = (typeof RUN_SURFACE_FILTERS)[number];

/** 「実行者」の絞り込み（すべて / 自分が実行 / 他の人が実行。既定はすべて。#1954）。 */
export const RUN_EXECUTOR_FILTERS = ["all", "mine", "others"] as const satisfies readonly RunExecutorFilter[];

export function runExecutorFilterLabel(value: RunExecutorFilter): string {
  switch (value) {
    case "mine":
      return t("history.filter.executor.mine");
    case "others":
      return t("history.filter.executor.others");
    default:
      return t("history.filter.all");
  }
}

export function runStatusView(status: RunStatus): BadgeView {
  switch (status) {
    case "success":
      return { variant: "success", label: t("history.status.success") };
    case "blocked":
      return { variant: "warning", label: t("history.status.blocked") };
    default:
      return { variant: "danger", label: t("history.status.error") };
  }
}

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

/** 所要時間（ミリ秒）を「12.3 秒」「2 分 5 秒」にする。記録が無ければ「—」。 */
export function formatRunElapsed(elapsedMs: number | null | undefined): string {
  if (elapsedMs == null || !Number.isFinite(elapsedMs)) return t("history.notRecorded");
  const seconds = elapsedMs / 1000;
  if (seconds < 60) return t("history.elapsed.seconds", { value: seconds.toFixed(1) });
  const whole = Math.round(seconds);
  return t("history.elapsed.minutes", { minutes: Math.floor(whole / 60), seconds: whole % 60 });
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

/**
 * 実行した人が自分でない行の「〈名前〉が実行」（#1954）。自分が実行した行は null（出さない）。
 * 持ち主の会話で system_admin が実行した行を、持ち主が見分ける。
 */
export function runExecutedByNote(run: Pick<RunSummary, "executed_by_me" | "executor_display_name">): string | null {
  if (run.executed_by_me) return null;
  return t("history.executedBy", { name: runExecutorLabel(run) });
}

/** 会話の持ち主の表示名。会話の無い実行は null（詳細に出さない）。 */
export function runConversationOwnerLabel(run: { conversation_owner_display_name: string | null }): string | null {
  if (run.conversation_owner_display_name == null) return null;
  return run.conversation_owner_display_name.trim() || t("history.user.missing");
}
