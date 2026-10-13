// AI要件確認の画面（#1439 / #1442 / #1715）。SQL 生成の画面（「AI要件確認」のボタンで開く）とチャット（送信の
// ときに自動で始める）が、この 1 つの部品で同じ表示・同じ操作を出す。基準は SQL 生成の画面の AI要件確認。
//
// 画面ごとに違うのは入口（いつ始めるか・session の作り方）と、確定した要件の行き先（SQL 生成の画面は質問の欄へ
// 反映、チャットは SQL の生成）の文言だけ。検査中・失敗（理由・「詳細」・もう一度確認・確認せずに生成・中止）・
// 1 問ずつの質問・確認済みの一覧からの直し・要件のまとめ・確定・終わった確認は、ここで決める。
import type { ReactNode } from "react";
import { CheckCircle2, Sparkles, X } from "lucide-react";

import { Banner, Button, ProcessingIndicator, StatusBadge } from "@production-ready/ui";

import { t } from "@/lib/i18n";

import {
  RequirementAnswerProgress,
  RequirementCheckFailure,
  RequirementErrorBanner,
  RequirementItemsList,
  RequirementQuestionForm,
  requirementPhaseBadge,
  RequirementStateBanners,
  RequirementSummary,
  type RequirementCheckRunner,
  type RequirementSessionController,
} from "./RequirementClarification";

/** 検査の段階（業務プロファイルの推薦 → 確定 → 要件の下書き）。チャットは下書きだけ。 */
export type RequirementStartPhase = "recommend_profile" | "confirm_profile" | "prepare_questions";

/** 確定した要件の行き先の文言（SQL 生成の画面: 質問の欄へ反映、チャット: SQL を生成）。 */
export interface RequirementApplyLabels {
  ready: string;
  apply: string;
  applyWithRemaining: string;
}

export const REQUIREMENT_APPLY_TO_QUESTION_LABELS: RequirementApplyLabels = {
  ready: t("nl2sql.clarification.ready"),
  apply: t("nl2sql.clarification.apply"),
  applyWithRemaining: t("nl2sql.clarification.applyWithRemaining"),
};

const START_PHASE_LABELS: Record<RequirementStartPhase, Parameters<typeof t>[0]> = {
  recommend_profile: "nl2sql.clarification.loading.recommendProfile",
  confirm_profile: "nl2sql.clarification.loading.confirmProfile",
  prepare_questions: "nl2sql.clarification.loading.prepareQuestions",
};

export interface RequirementClarificationViewProps {
  controller: RequirementSessionController;
  check: RequirementCheckRunner;
  startPhase: RequirementStartPhase;
  /** 「もう一度確認」（検査をやり直す）。 */
  onRetry: () => void;
  /** 「確認せずに SQL を生成」。 */
  onSkip: () => void;
  /** 確かめた要件で次へ進む（反映 / SQL の生成）。 */
  onApply: () => void;
  /** 「確認を中止して閉じる」。 */
  onClose: () => void;
  labels: RequirementApplyLabels;
  /** 入口の前段（SQL 生成の画面の業務プロファイルの選択）。出している間は下の「中止」の行を出さない。 */
  startSlot?: ReactNode;
  /** 操作部品の高さ（置く場所の行に合わせる。SQL 生成の画面は lg、チャットは sm）。 */
  size: "sm" | "lg";
  /** 要素の id の接頭辞（同じ画面に 2 つ置かないので、画面ごとに 1 つ）。 */
  idPrefix: string;
}

export function RequirementClarificationView({
  controller,
  check,
  startPhase,
  onRetry,
  onSkip,
  onApply,
  onClose,
  labels,
  startSlot,
  size,
  idPrefix,
}: RequirementClarificationViewProps) {
  const { session, busyAction, error, sessionEnded, state, draft, activeItem, showApply } = controller;
  const badge = state ? requirementPhaseBadge(state) : null;
  const titleId = `${idPrefix}-guided-clarification-title`;
  const closeButton = (
    <Button
      type="button"
      variant="ghost"
      size={size}
      loading={busyAction === "cancel"}
      disabled={busyAction === "answer" || busyAction === "apply"}
      onClick={onClose}
      icon={X}
      data-testid="nl2sql-clarification-close"
    >
      {t("nl2sql.clarification.close")}
    </Button>
  );
  return (
    <section
      aria-labelledby={titleId}
      className="grid min-w-0 gap-4 rounded-md border border-accent-emphasis bg-surface p-4"
      data-testid="nl2sql-guided-clarification"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Sparkles size={20} className="text-accent-fg" aria-hidden="true" />
          <h3 id={titleId} className="text-base font-semibold text-fg">
            {t("nl2sql.clarification.title")}
          </h3>
          {badge && !sessionEnded ? <StatusBadge variant={badge.variant} label={badge.label} /> : null}
          {state ? (
            <span className="text-xs text-fg-muted" data-testid="nl2sql-clarification-review-round">
              {t("nl2sql.clarification.reviewRound", {
                round: state.review_round,
                max: state.max_review_rounds,
              })}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          {state?.message_ja || t("nl2sql.clarification.description")}
        </p>
      </div>

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {state?.message_ja}
      </div>

      {!sessionEnded ? <RequirementErrorBanner error={error} /> : null}
      {check.checkError ? (
        <RequirementCheckFailure
          error={check.checkError}
          onRetry={onRetry}
          onSkip={onSkip}
          cancelAction={closeButton}
          size={size}
          testId="nl2sql-guided-clarification-error"
        />
      ) : null}
      {sessionEnded ? (
        <Banner severity="warning">{session?.error_message_ja || t("nl2sql.clarification.ended")}</Banner>
      ) : null}

      {check.checking ? (
        <div className="rounded-md border border-border bg-surface-sunken p-4" data-start-phase={startPhase}>
          <ProcessingIndicator
            active
            operationKey="guided-clarification-start"
            label={t(START_PHASE_LABELS[startPhase])}
            placement="panel"
            testId="nl2sql-guided-start-progress"
          />
        </div>
      ) : null}
      <RequirementAnswerProgress active={busyAction === "answer"} />

      {startSlot}

      <RequirementStateBanners state={state} unresolvedCount={controller.unresolvedCount} />

      <RequirementQuestionForm
        controller={controller}
        size={size}
        idPrefix={`${idPrefix}-clarification`}
        applyWithRemainingLabel={labels.applyWithRemaining}
        onApplyWithRemaining={onApply}
        trailingActions={closeButton}
      />

      {state && draft ? <RequirementItemsList controller={controller} /> : null}

      <RequirementSummary controller={controller} idPrefix={idPrefix} />

      {showApply && !activeItem ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <span className="flex items-center gap-2 text-sm text-success-fg">
            <CheckCircle2 size={16} aria-hidden="true" />
            {labels.ready}
          </span>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size={size}
              loading={busyAction === "apply"}
              disabled={Boolean(busyAction && busyAction !== "apply")}
              onClick={onApply}
              icon={Sparkles}
              data-testid="nl2sql-clarification-apply"
            >
              {labels.apply}
            </Button>
            {closeButton}
          </div>
        </div>
      ) : null}

      {!activeItem && !showApply && !startSlot && !check.checkError ? (
        <div className="flex flex-wrap justify-end border-t border-border pt-4">{closeButton}</div>
      ) : null}
    </section>
  );
}
