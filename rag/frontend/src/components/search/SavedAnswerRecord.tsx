import { createTraceDetailsLabels,  TraceDetails, AnswerFooter, FormStatus, Skeleton, TimedLoadingState } from "@production-ready/ui";

import { ApiError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useAnswerRecord } from "@/lib/queries";

import { AnswerCitations } from "./CitationList";
import { AnswerDetailsPanel } from "./AnswerDetailsPanel";

/**
 * 保存された回答 1 件(質問・回答・引用・根拠パネル)。
 *
 * 回答の記録は 1 件ずつ削除しない（保存期間を過ぎた記録は自動で消える。#2348）。
 */
export function SavedAnswerRecord({
  traceId,
  searchAnswerProfileId,
  showAnswer = true,
  showCitations = true,
  detailsTitle,
}: {
  traceId: string;
  searchAnswerProfileId: string;
  showAnswer?: boolean;
  /** 引用の一覧を出すか。チャットは回答の枠に「AI に渡した N 件」の一覧を持つので出さない（#1202）。 */
  showCitations?: boolean;
  /** 回答の記録の詳細の見出し。省略すると見出しを出さない（チャットの「処理の経過」の中の「処理の詳細」。#2096 / #2325）。 */
  detailsTitle?: string;
}) {
  const detail = useAnswerRecord(traceId);

  if (detail.isPending) {
    return (
      <TimedLoadingState
        label={t("search.history.detailLoading")}
        operationKey="search-history-detail-load"
        framed={false}
        testId="search-history-detail-loading"
      >
        <Skeleton className="h-24 w-full" />
      </TimedLoadingState>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <FormStatus
        tone="danger"
        message={
          detail.error instanceof ApiError
            ? detail.error.message
            : t("search.history.loadError")
        }
      />
    );
  }
  const record = detail.data;
  return (
    <div className="space-y-3 rounded-md border border-border bg-surface-sunken p-3">
      <p className="break-words text-xs text-fg-muted">
        {showAnswer
          ? t("search.history.question", { question: record.question })
          : t("search.history.savedAt", { value: formatDateTime(record.created_at) })}
      </p>
      {showAnswer ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
          {record.answer}
        </p>
      ) : null}
      {/*
        回答に付けた注意書き（そのときの文。#2405）→ 回答の根拠（チャット・RAG 検索と同じ部品。#2097・#2388）の順
        （3 製品のチャットと同じ共通の並び。#2707）。
      */}
      <AnswerFooter
        notice={showAnswer ? record.notice : null}
        noticeLabel={t("answer.notice.label")}
        evidence={
          showCitations && record.citations.length > 0 ? (
            <AnswerCitations
              citations={record.citations}
              idPrefix={`answer-record-${record.trace_id}`}
              cardProps={{
                traceId: record.trace_id,
                searchAnswerProfileId,
                sourceSurface: record.surface,
              }}
            />
          ) : null
        }
      />

      {/* 根拠（引用の一覧）を先、回答の記録の詳細を後に並べる（#1417。page-archetypes.md §6.1）。 */}
      <TraceDetails labels={createTraceDetailsLabels(t)} summary={record.answer_diagnostics?.trace_summary} />
      <AnswerDetailsPanel
        diagnostics={record.answer_diagnostics}
        traceId={record.evaluation_available ? record.trace_id : null}
        evaluation={record.evaluation}
        title={detailsTitle}
      />
    </div>
  );
}
