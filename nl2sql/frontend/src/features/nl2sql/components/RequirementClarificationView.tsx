// 回答前の確認の画面（#1439 / #1442 / #1715 / #1761）。SQL 生成の画面（「回答前の確認」のボタンで開く）とチャット
// （送信のときに自動で始める）が、この 1 つの部品で同じ表示・同じ操作・同じ文言を出す。
//
// 画面ごとに違うのは入口（いつ始めるか・session の作り方・SQL 生成の画面の業務プロファイルの選択）と、置く場所に
// 合わせた操作部品の高さだけ。検査中・失敗（理由・「詳細」・もう一度確認・確認せずに生成・確認をやめる）・1 件
// ずつの確認事項・確認済みの一覧からの直し・要件のまとめ・確かめて生成するカード（対象の選び直し・その他（自由
// 入力）・「この内容で SQL を生成して実行」・「確認をやめる」）・終わった確認は、ここで決める。確認事項が 0 件でも、
// 利用者が「この内容で SQL を生成して実行」を押すまで要件を生成に渡さない（#1761）。確認事項と確かめて生成する
// カードは 3 製品共通の回答前の確認のカード（`ClarificationCard`。#1839）で出す。
import type { ReactNode } from "react";
import { CheckCircle2, Sparkles, X } from "lucide-react";

import { Banner, Button, ProcessingIndicator, StatusBadge } from "@production-ready/ui";

import { t } from "@/lib/i18n";

import {
  RequirementAnswerProgress,
  RequirementCard,
  RequirementCheckFailure,
  RequirementErrorBanner,
  RequirementItemsList,
  requirementPhaseBadge,
  RequirementStateBanners,
  RequirementSummary,
  type RequirementCheckRunner,
  type RequirementSessionController,
} from "./RequirementClarification";

/** 検査の段階（業務プロファイルの推薦 → 確定 → 要件の下書き）。チャットは下書きだけ。 */
export type RequirementStartPhase = "recommend_profile" | "confirm_profile" | "prepare_questions";

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
  /** 「確認をやめる」（チャットは質問を入力欄に戻す）。 */
  onClose: () => void;
  /**
   * SQL を実行できる（実行の権限がある）か。無ければ確定の操作は「この内容で SQL を生成」（生成だけ）にする。
   * 画面ごとの文言の違いではなく、権限による違い（#1761。文言は両方の画面で同じ）。
   */
  canExecuteSql?: boolean;
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
  canExecuteSql = true,
  startSlot,
  size,
  idPrefix,
}: RequirementClarificationViewProps) {
  const { session, busyAction, error, sessionEnded, state, draft, activeItem, showApply } = controller;
  const badge = state ? requirementPhaseBadge(state) : null;
  // 確定して進む操作の文言は、SQL 生成の画面とチャットで同じ（#1761。押すと確定した要件で SQL を生成して実行する）。
  const applyLabel = canExecuteSql ? t("chat.requirement.generate") : t("chat.requirement.generateOnly");
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

      {activeItem ? (
        <RequirementCard
          controller={controller}
          size={size}
          applyLabel={applyLabel}
          onApply={onApply}
          onSkip={onSkip}
          onClose={onClose}
          idPrefix={idPrefix}
        />
      ) : null}

      {state && draft ? <RequirementItemsList controller={controller} /> : null}

      <RequirementSummary controller={controller} idPrefix={idPrefix} />

      {showApply && !activeItem ? (
        <div className="grid min-w-0 gap-3 border-t border-border pt-4">
          <p className="flex items-center gap-2 text-sm text-success-fg">
            <CheckCircle2 size={16} aria-hidden="true" className="shrink-0" />
            {t("chat.requirement.ready")}
          </p>
          <RequirementCard
            controller={controller}
            size={size}
            applyLabel={applyLabel}
            onApply={onApply}
            onSkip={onSkip}
            onClose={onClose}
            idPrefix={idPrefix}
          />
        </div>
      ) : null}

      {!activeItem && !showApply && !startSlot && !check.checkError ? (
        <div className="flex flex-wrap justify-end border-t border-border pt-4">{closeButton}</div>
      ) : null}
    </section>
  );
}
