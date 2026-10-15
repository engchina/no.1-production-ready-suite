import { Banner, StatusBadge } from "@production-ready/ui";
import { BookCheck } from "lucide-react";

import {
  confidenceVariant,
  outcomeBadge,
  parseAnswerDiagnostics,
  type AnswerDiagnostics,
} from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { AnswerGuideConditions } from "./AnswerGuideConditions";
import { AnswerRecordEvaluation } from "./AnswerRecordEvaluation";
import { extractionFieldConditionLabel } from "./extraction-field-filters";

/**
 * 回答の記録の詳細（対応・信頼度・人手確認・使ったモデルなど）。工程の一覧は「処理の経過」
 * （`AnswerProcess`）が出す（#2096。工程を 2 か所に出さない）。チャット・RAG 検索では「処理の経過」の
 * 「詳細」の中に見出しなしで置き、実行履歴では見出し（`title`）を付けて置く。根拠の一覧とまとまりは
 * `CitationList`（#2097）。
 * traceId を渡すと、保存した回答を標準回答で評価する欄も出す。
 */
export function AnswerDetailsPanel({
  diagnostics,
  traceId,
  evaluation,
  title,
  showAutoFieldFilter = true,
  showNotices = true,
}: {
  diagnostics: unknown;
  traceId?: string | null;
  evaluation?: unknown;
  /** 見出し。省略すると見出しを出さない（「処理の経過」の「詳細」の中）。 */
  title?: string;
  /** 質問から読み取った条件（#652）を出すか。RAG 検索は外せるチップで出すので出さない。 */
  showAutoFieldFilter?: boolean;
  /** 回答を読むときに要る案内（`AnswerNotices`）を出すか。回答の本文の下に出したときは false。 */
  showNotices?: boolean;
}) {
  const data = parseAnswerDiagnostics(diagnostics);
  if (!data) return null;
  const outcome = outcomeBadge(data.outcome);
  const models = [
    data.models.llm
      ? {
          key: "llm",
          label: t("search.answerDetails.model.llm"),
          value: data.models.llm.label,
          title: data.models.llm.modelId,
        }
      : null,
    data.models.vision
      ? {
          key: "vision",
          label: t("search.answerDetails.model.vision"),
          value: data.models.vision.label,
          title: data.models.vision.modelId,
        }
      : null,
    data.models.embedding
      ? { key: "embedding", label: t("search.answerDetails.model.embedding"), value: data.models.embedding }
      : null,
    data.models.rerank
      ? { key: "rerank", label: t("search.answerDetails.model.rerank"), value: data.models.rerank }
      : null,
  ].flatMap((item) => (item ? [item] : []));
  return (
    <section
      className={title ? "space-y-3 border-t border-border pt-3" : "space-y-3"}
      aria-label={title ?? t("answer.process.details")}
    >
      <div className="flex flex-wrap items-center gap-2">
        {title ? <span className="text-sm font-semibold text-fg">{title}</span> : null}
        {outcome ? (
          <span data-testid="answer-outcome-badge">
            <StatusBadge variant={outcome.variant} label={t(outcome.key)} />
          </span>
        ) : null}
        {data.guide ? (
          <span data-testid="answer-guide-badge" data-guide-id={data.guide.guideId || undefined}>
            <StatusBadge
              variant="neutral"
              label={t("search.answerDetails.guide", {
                title: data.guide.title,
                revision: data.guide.revision,
              })}
            />
          </span>
        ) : null}
        {data.confidence ? (
          <StatusBadge
            variant={confidenceVariant(data.confidence)}
            label={t("search.answerDetails.confidence", { value: data.confidence })}
          />
        ) : null}
        {data.approvedFaq ? (
          <StatusBadge variant="info" label={t("search.answerDetails.faqSource.badge")} />
        ) : null}
        {data.needsHumanReview ? (
          <StatusBadge
            variant="warning"
            label={t("search.answerDetails.humanReview")}
          />
        ) : null}
        {data.questionType.map((value) => (
          <StatusBadge
            key={value}
            variant="neutral"
            label={t("search.answerDetails.questionType", { value })}
          />
        ))}
      </div>
      {data.guide ? <AnswerGuideConditions guide={data.guide} /> : null}
      {showNotices ? <AnswerNoticeBanners data={data} /> : null}
      {data.approvedFaq ? (
        // 承認済み FAQ から回答したときの出典。生成した回答と照らし合わせられるよう、回答した時点の
        // 原文を言い換えずに出す（#737）。
        <section
          className="space-y-2 rounded-md border border-border bg-surface p-3"
          aria-label={t("search.answerDetails.faqSource.title")}
          data-testid="answer-approved-faq-source"
        >
          <p className="flex items-center gap-1.5 text-xs font-medium text-fg-muted">
            <BookCheck size={14} className="text-accent-fg" aria-hidden />
            {t("search.answerDetails.faqSource.title")}
          </p>
          <dl className="space-y-2 text-sm">
            <div>
              <dt className="text-xs text-fg-muted">{t("search.answerDetails.faqSource.question")}</dt>
              <dd className="break-words font-medium text-fg">{data.approvedFaq.question}</dd>
            </div>
            <div>
              <dt className="text-xs text-fg-muted">{t("search.answerDetails.faqSource.answer")}</dt>
              <dd className="whitespace-pre-wrap break-words leading-relaxed text-fg">
                {data.approvedFaq.answer || (
                  <span className="text-xs text-fg-muted">
                    {t("search.answerDetails.faqSource.answerMissing")}
                  </span>
                )}
              </dd>
            </div>
          </dl>
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("search.answerDetails.faqSource.note")}
          </p>
        </section>
      ) : null}
      {models.length ? (
        <div
          className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
          data-testid="answer-models"
        >
          <span className="font-medium text-fg-muted">{t("search.answerDetails.models")}</span>
          <dl className="contents">
            {models.map((model) => (
              <div key={model.key} className="flex min-w-0 items-center gap-1.5">
                <dt className="text-fg-muted">{model.label}</dt>
                <dd className="break-all font-medium text-fg" title={model.title}>
                  {model.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {showAutoFieldFilter && data.autoFieldFilter ? (
        // 質問から読み取って検索に足した条件（#652）。
        <div className="space-y-0.5 text-xs leading-relaxed text-fg-muted" data-testid="auto-field-filter">
          <p className="break-words">
            {t("search.answerDetails.autoFieldFilter")}:{" "}
            {data.autoFieldFilter.conditions.map(extractionFieldConditionLabel).join(" / ")}
          </p>
          {data.autoFieldFilter.relaxed ? (
            <p>{t("search.answerDetails.autoFieldFilterRelaxed")}</p>
          ) : null}
        </div>
      ) : null}
      {data.rewrittenQuestion ? (
        <p className="break-words text-xs leading-relaxed text-fg-muted">
          {t("search.answerDetails.rewritten", { question: data.rewrittenQuestion })}
        </p>
      ) : null}
      {data.insufficientReason ? (
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("search.answerDetails.insufficient", { reason: data.insufficientReason })}
        </p>
      ) : null}
      {data.reasoningSummary ? (
        <p className="break-words text-xs leading-relaxed text-fg-muted">
          {t("search.answerDetails.reasoning", { reason: data.reasoningSummary })}
        </p>
      ) : null}
      {/* 根拠の構成（まとまりごとの表示）は、AI に渡した箇所の一覧（CitationList）の「まとまりごと」に出す。件数の単位を
          1 か所にそろえるため、ここには出さない（#2097）。 */}
      {data.generatedQueries.length ? (
        <p className="break-words text-xs text-fg-muted">
          {t("search.answerDetails.queries")}: {data.generatedQueries.join(" / ")}
        </p>
      ) : null}
      {data.graphExpansion ? (
        // 関係情報で加えた根拠の数と、使った見出し（#2055）。
        <p className="break-words text-xs text-fg-muted" data-testid="answer-graph-expansion">
          {t("search.answerDetails.graphExpansion", { count: data.graphExpansion.addedCount })}
          {data.graphExpansion.entities.length
            ? `: ${data.graphExpansion.entities
                .map((entity) =>
                  t("search.answerDetails.graphExpansionEntity", {
                    name: entity.name,
                    count: entity.addedCount,
                  })
                )
                .join(" / ")}`
            : ""}
        </p>
      ) : null}
      {traceId ? (
        <AnswerRecordEvaluation key={traceId} traceId={traceId} evaluation={evaluation} />
      ) : null}
    </section>
  );
}

/**
 * 回答を読むときに要る案内（公開の業務ガイドを読めなかった・資料では決まらない値がある）。回答の本文の下に
 * 出す（「詳細」に畳まない。#2096）。
 */
export function AnswerNotices({ diagnostics }: { diagnostics: unknown }) {
  const data = parseAnswerDiagnostics(diagnostics);
  if (!data || (!data.guideLoadFailed && !data.externalDataRequired && !data.externalDataItems.length)) return null;
  return (
    <div className="space-y-2">
      <AnswerNoticeBanners data={data} />
    </div>
  );
}

function AnswerNoticeBanners({ data }: { data: AnswerDiagnostics }) {
  return (
    <>
      {data.guideLoadFailed ? (
        // 公開の業務ガイドを読めず、使わずに答えた（ガイドが無かったときと区別する。#1278 / #1287）。
        <div data-testid="answer-guide-load-failed">
          <Banner severity="warning" title={t("search.answerDetails.guideLoadFailed.title")}>
            <p>{t("search.answerDetails.guideLoadFailed.body")}</p>
          </Banner>
        </div>
      ) : null}
      {data.externalDataRequired || data.externalDataItems.length ? (
        // 資料では決まらない値（rag_poc の「外部データ確認」。#651）。
        <Banner severity="warning" title={t("search.answerDetails.externalData.title")}>
          <p>{t("search.answerDetails.externalData.hint")}</p>
          {data.externalDataItems.length ? (
            <ul className="mt-1 list-disc pl-5">
              {data.externalDataItems.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          ) : null}
        </Banner>
      ) : null}
    </>
  );
}
