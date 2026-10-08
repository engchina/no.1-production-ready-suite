import { useId, type ReactNode } from "react";
import { Info, ShieldCheck } from "lucide-react";
import { Disclosure, StatusBadge, type StatusVariant } from "@engchina/production-ready-ui";

import type {
  AnswerReview,
  ConditionSource,
  ReviewOutcome,
  ReviewValidation,
  UnverifiedClaimKind,
  UnverifiedPoint,
} from "@/lib/answer-review";
import { t, type I18nKey } from "@/lib/i18n";

/**
 * 回答の確かめ（#1286）。回答の下に、確かめた条件・確認待ちの質問・使った業務ガイド・資料で確かめた結果を
 * 業務の言葉で畳んで出す（出典・使ったツールと同じ plain の Disclosure）。資料で確かめた結果は畳んでいても
 * 見出しの StatusBadge で分かるようにする。回答の対応（条件付きの回答・確認が必要など。#1314）も、検証のバッジと
 * 並べて見出しに出す。成果物の JSON・内部の語は出さない（handoff §13。元の JSON は実行の詳細の成果物で管理者だけが見る）。
 */
export function AnswerReviewPanel({ review, testId }: { review: AnswerReview; testId: string }) {
  const hasDetails =
    review.validation !== null ||
    review.conditions.length > 0 ||
    review.clarifications.length > 0 ||
    review.guide !== null ||
    review.gaps.length > 0;
  // 見出しとバッジは同じ行に置き、狭い幅ではバッジを次の行へ折り返す（見出しの文字を縦に潰さない）。
  const heading = (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1">
      <span className="whitespace-nowrap">{t("chat.review.title")}</span>
      {review.outcome ? <OutcomeBadge outcome={review.outcome} testId={`${testId}-outcome`} /> : null}
      {review.validation ? <ValidationBadge validation={review.validation} /> : null}
    </span>
  );
  return (
    <>
      {review.limitReached ? <LimitNote testId={`${testId}-limit`} /> : null}
      {hasDetails ? (
        <Disclosure
          variant="plain"
          size="sm"
          icon={ShieldCheck}
          summary={heading}
          summaryProps={{ "data-testid": `${testId}-summary` }}
          data-testid={testId}
        >
          <AnswerReviewSections review={review} />
        </Disclosure>
      ) : review.outcome ? (
        // 対応だけがある回答（検証も支援タスクの状態も無い）は、開いても中身が無いので畳まずに見出しだけを出す。
        <p className="flex items-center gap-1.5 text-xs font-medium text-fg" data-testid={testId}>
          <ShieldCheck size={14} className="shrink-0 text-fg-muted" aria-hidden="true" />
          <span className="min-w-0 break-words">{heading}</span>
        </p>
      ) : null}
    </>
  );
}

/** 回答の確かめの中身（チャットの回答の下と、実行の詳細の成果物で使う）。 */
export function AnswerReviewSections({ review }: { review: AnswerReview }) {
  return (
    <div className="space-y-3 text-xs [overflow-wrap:anywhere]">
      {review.conditions.length > 0 ? (
        <ReviewGroup title={t("chat.review.conditions")} testId="answer-review-conditions">
          <ul className="space-y-1">
            {review.conditions.map((condition) => (
              <li key={condition.key} className="break-words text-fg">
                <span className="font-medium">{condition.label}</span>
                {t("chat.review.separator")}
                {condition.value}
                {condition.source ? (
                  <span className="text-fg-muted">{t("chat.review.sourceWrap", { source: sourceLabel(condition.source) })}</span>
                ) : null}
                {condition.previousValue ? (
                  <span className="block text-fg-muted">
                    {t("chat.review.previousValue", { value: condition.previousValue })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </ReviewGroup>
      ) : null}

      {review.clarifications.length > 0 ? (
        <ReviewGroup title={t("chat.review.clarifications")} testId="answer-review-clarifications">
          <ul className="space-y-1">
            {review.clarifications.map((item) => (
              <li key={item.key} className="break-words text-fg">
                {item.question}
                {item.options.length > 0 ? (
                  <span className="block text-fg-muted">
                    {t("chat.review.options", { options: item.options.join(" / ") })}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </ReviewGroup>
      ) : null}

      {review.guide ? (
        <ReviewGroup title={t("chat.review.guide")} testId="answer-review-guide">
          <p className="break-words text-fg">
            {review.guide.title || t("chat.review.guideUntitled")}
            {review.guide.revision ? (
              <span className="text-fg-muted">{t("chat.review.guideRevision", { revision: review.guide.revision })}</span>
            ) : null}
          </p>
        </ReviewGroup>
      ) : null}

      {review.gaps.length > 0 ? (
        <ReviewGroup title={t("chat.review.gaps")} testId="answer-review-gaps">
          <ul className="list-disc space-y-1 pl-4">
            {review.gaps.map((gap, index) => (
              <li key={`${index}:${gap}`} className="break-words text-fg">
                {gap}
              </li>
            ))}
          </ul>
        </ReviewGroup>
      ) : null}

      {review.validation ? <ValidationGroup validation={review.validation} /> : null}
    </div>
  );
}

function ReviewGroup({ title, testId, children }: { title: string; testId: string; children: ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className="space-y-1" data-testid={testId}>
      <p id={id} className="font-medium text-fg-muted">
        {title}
      </p>
      {children}
    </div>
  );
}

function ValidationGroup({ validation }: { validation: ReviewValidation }) {
  return (
    <ReviewGroup title={t("chat.review.validation")} testId="answer-review-validation">
      <div className="space-y-1.5">
        <ValidationBadge validation={validation} />
        <p className="break-words text-fg">{validationMessage(validation)}</p>
        {validation.points.length > 0 ? (
          <div className="space-y-1">
            <p className="font-medium text-fg">{t("chat.review.unverifiedPoints")}</p>
            <ul className="list-disc space-y-1 pl-4" data-testid="answer-review-unverified">
              {validation.points.map((point, index) => (
                <li key={index} className="break-words text-fg">
                  <PointText point={point} />
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </ReviewGroup>
  );
}

function PointText({ point }: { point: UnverifiedPoint }) {
  switch (point.kind) {
    case "claim":
      return (
        <>
          <span className="font-medium">{t(CLAIM_LABEL[point.claim])}</span>
          {t("chat.review.separator")}
          {point.quote ? t("chat.review.quote", { quote: point.quote }) : null}
          {point.reason ? <span className="text-fg-muted">{t("chat.review.reasonWrap", { reason: point.reason })}</span> : null}
        </>
      );
    case "finding":
      return <>{point.message}</>;
    case "stale":
      return <>{t("chat.review.staleEvidence", { count: point.count })}</>;
    case "missing":
      return <>{t("chat.review.missingEvidence", { count: point.count })}</>;
  }
}

/** 予算の上限に達したときの控えめな案内（内部の語は出さない）。 */
export function LimitNote({ testId }: { testId: string }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-fg-muted" data-testid={testId}>
      <Info size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
      <span className="min-w-0 break-words">{t("chat.review.limitReached")}</span>
    </p>
  );
}

const CLAIM_LABEL: Record<UnverifiedClaimKind, I18nKey> = {
  contradicted: "chat.review.claim.contradicted",
  unsupported: "chat.review.claim.unsupported",
  citation_error: "chat.review.claim.citationError",
  unassessed: "chat.review.claim.unassessed",
};

function sourceLabel(source: Exclude<ConditionSource, null>): string {
  return source === "user" ? t("chat.review.source.user") : t("chat.review.source.question");
}

/**
 * 回答の対応 → StatusBadge の対応表（#1314）。文言と色は RAG の回答の詳細（`outcomeBadge`。#1252）と同じにする
 * （3 製品で同じ機能は同じ書き方）。「答えた」は出さない（`ReviewOutcome` に含めない）。
 */
const OUTCOME_BADGE: Record<ReviewOutcome, { variant: StatusVariant; label: I18nKey }> = {
  conditional: { variant: "warning", label: "chat.review.outcome.conditional" },
  needs_clarification: { variant: "info", label: "chat.review.outcome.needsClarification" },
  needs_environment_data: { variant: "warning", label: "chat.review.outcome.needsEnvironmentData" },
  needs_human: { variant: "warning", label: "chat.review.outcome.needsHuman" },
  insufficient_evidence: { variant: "danger", label: "chat.review.outcome.insufficientEvidence" },
};

/** 回答の対応のバッジ（チャットの回答の確かめの見出しと、実行の詳細の回答で使う。アイコン付き）。 */
export function OutcomeBadge({ outcome, testId }: { outcome: ReviewOutcome; testId: string }) {
  const { variant, label } = OUTCOME_BADGE[outcome];
  return (
    <span className="inline-flex" data-testid={testId}>
      <StatusBadge variant={variant} label={t(label)} />
    </span>
  );
}

/** 資料で確かめた結果のバッジ（状態の対応表。色だけに頼らず StatusBadge のアイコンを付ける）。 */
export function ValidationBadge({ validation }: { validation: ReviewValidation }) {
  const { variant, label } = validationBadge(validation);
  return <StatusBadge variant={variant} label={label} />;
}

function validationBadge(validation: ReviewValidation): { variant: StatusVariant; label: string } {
  switch (validation.state) {
    case "verified":
      return { variant: "success", label: t("chat.review.state.verified") };
    case "withheld":
      if (validation.withheldAll) return { variant: "warning", label: t("chat.review.state.withheldAll") };
      if (validation.withheldClaims > 0) {
        return { variant: "warning", label: t("chat.review.state.withheld", { count: validation.withheldClaims }) };
      }
      return { variant: "warning", label: t("chat.review.state.unverifiedPoints") };
    case "unvalidated":
      return { variant: "warning", label: t("chat.review.state.unvalidated") };
    case "skipped":
      return { variant: "neutral", label: t("chat.review.state.skipped") };
  }
}

function validationMessage(validation: ReviewValidation): string {
  switch (validation.state) {
    case "verified":
      return t("chat.review.message.verified");
    case "withheld":
      if (validation.withheldAll) return t("chat.review.message.withheldAll");
      if (validation.withheldClaims > 0) {
        return t("chat.review.message.withheld", { count: validation.withheldClaims });
      }
      return t("chat.review.message.unverifiedPoints");
    case "unvalidated":
      return validation.cause === "noEvidence"
        ? t("chat.review.message.noEvidence")
        : t("chat.review.message.unvalidated");
    case "skipped":
      if (validation.cause === "emptyAnswer") return t("chat.review.message.skippedEmpty");
      if (validation.cause === "clarification") return t("chat.review.message.skippedClarification");
      return t("chat.review.message.skipped");
  }
}
