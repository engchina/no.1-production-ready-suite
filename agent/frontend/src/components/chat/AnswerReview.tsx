import { useId, type ReactNode } from "react";
import { Info, ShieldCheck } from "lucide-react";
import { Disclosure, StatusBadge, TEXT_ICON_SIZE, type StatusVariant } from "@production-ready/ui";

import type { AnswerReview, ConditionSource, ReviewOutcome } from "@/lib/answer-review";
import { t, type I18nKey } from "@/lib/i18n";

/**
 * 回答の確認事項（#1286・#2361）。回答の下に、回答の対応のバッジと、確かめた条件・確認待ちの質問・
 * 使った業務ガイド・足りない情報を、見られるようにする（出典と同じ plain の Disclosure）。
 * 成果物の JSON・内部の語は出さない（handoff §13）。
 */
export function AnswerReviewPanel({ review, testId }: { review: AnswerReview; testId: string }) {
  const hasDetails =
    review.conditions.length > 0 ||
    review.clarifications.length > 0 ||
    review.guide !== null ||
    review.gaps.length > 0;
  // 見出しとバッジは同じ行に置き、狭い幅ではバッジを次の行へ折り返す（見出しの文字を縦に潰さない）。
  // バッジは上下にはみ出させて行の高さを文字の行の高さに保つ（見出しのアイコンを文字の 1 行目の中心にそろえる。#2371）。
  const heading = (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 gap-y-1 [&_[data-status-variant]]:-my-0.5">
      <span className="whitespace-nowrap">{t("chat.review.title")}</span>
      {review.outcome ? <OutcomeBadge outcome={review.outcome} testId={`${testId}-outcome`} /> : null}
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
        // 開いても中身が無い（対応のバッジだけ）ときは、畳まずに見出しだけを出す（⌄ を出さない）。
        <p className="flex items-start gap-1.5 text-xs font-medium text-fg" data-testid={testId}>
          <span className="pr-icon-line text-fg-muted">
            <ShieldCheck size={TEXT_ICON_SIZE.xs} aria-hidden="true" />
          </span>
          <span className="min-w-0 break-words">{heading}</span>
        </p>
      ) : null}
    </>
  );
}

/** 回答の確認事項の中身（チャットの回答の下で使う）。 */
function AnswerReviewSections({ review }: { review: AnswerReview }) {
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

/** 予算の上限に達したときの控えめな案内（内部の語は出さない）。 */
function LimitNote({ testId }: { testId: string }) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-fg-muted" data-testid={testId}>
      {/* 印は文字の大きさで、1 行目の中心にそろえる（#2371）。 */}
      <span className="pr-icon-line">
        <Info size={TEXT_ICON_SIZE.xs} aria-hidden="true" />
      </span>
      <span className="min-w-0 break-words">{t("chat.review.limitReached")}</span>
    </p>
  );
}

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

/** 回答の対応のバッジ（チャットの回答の確認事項の見出しと、実行の詳細の回答で使う。アイコン付き）。 */
export function OutcomeBadge({ outcome, testId }: { outcome: ReviewOutcome; testId: string }) {
  const { variant, label } = OUTCOME_BADGE[outcome];
  return (
    <span className="inline-flex" data-testid={testId}>
      <StatusBadge variant={variant} label={t(label)} />
    </span>
  );
}
