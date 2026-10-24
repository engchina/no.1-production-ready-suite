import { createTraceDetailsLabels, ChatProgress, RunProcessDetails, type ChatProgressStep } from "@production-ready/ui";

import { t } from "@/lib/i18n";
import { AnswerDetailsPanel } from "./AnswerDetailsPanel";
import { SavedAnswerRecord } from "./SavedAnswerRecord";

/**
 * 回答（チャット・RAG 検索）の「処理の経過」（#2096）。3 製品共通の `ChatProgress`（工程の一覧。実行中は開いて
 * 行が増え、今の行に Spinner と経過時間）と、その中の末尾の「処理の詳細」（回答の記録の詳細。技術的な詳細は既定で
 * 閉じる。messaging.md §10）を 1 か所にまとめる。
 *
 * 「処理の詳細」は回答の補足ではなく処理の経過に属する技術的な詳細なので、処理の経過を開いたときだけタイムラインの
 * 後ろに出し、閉じると一緒に隠れる（#2325。回答の本文との間に独立して置かない）。段階の記録の無い回答（段階が 0 件）は
 * 処理の経過の行が無いので、「処理の詳細」を単独で出す。
 *
 * チャットと RAG 検索は、この 1 つの部品で同じ位置（回答・検索結果の上。#2115）・同じ開閉・同じ「処理の詳細」を出す
 * （#2118。チャットの形にそろえる）。工程の一覧は実行中は開き、完了したら閉じる（`ChatProgress` の既定。3 製品で同じ。#2265）。
 * 「処理の詳細」は回答の記録の診断（`diagnostics`）があればその詳細、無ければ
 * 保存済みの回答の記録（`savedRecord`。会話を開き直したとき）を読み直して出す。回答の記録の削除はチャット・RAG 検索では
 * 出さない（#2325）。
 */
export function AnswerProcess({
  steps,
  active,
  startedAt,
  elapsedMs,
  reconnecting,
  progressKey,
  diagnostics,
  traceSummary,
  savedRecord,
  showNotices = false,
  showAutoFieldFilter,
  testId,
  className,
}: {
  steps: ChatProgressStep[];
  /** 処理中か。省略時は段階から決める。 */
  active?: boolean;
  /** 処理全体の開始（実行中の経過時間の起点）。 */
  startedAt?: string | number | null;
  /** 完了後の全体の所要時間。省略時は段階の時刻から。 */
  elapsedMs?: number | null;
  reconnecting?: boolean;
  progressKey?: string | null;
  /** 回答の記録の診断（`diagnostics.answer`）。完了後の「処理の詳細」に出す。実行中は渡さない。 */
  diagnostics?: unknown;
  traceSummary?: unknown;
  /** 診断の無い保存済みの回答（会話を開き直したとき）。回答の記録を読み直して「処理の詳細」に出す。 */
  savedRecord?: { traceId: string; searchAnswerProfileId: string } | null;
  /** 回答を読むときに要る案内を「処理の詳細」に出すか。回答の本文の下に出すときは false（既定）。 */
  showNotices?: boolean;
  /** 質問から読み取った条件を「処理の詳細」に出すか。RAG 検索は外せるチップで出すので出さない。 */
  showAutoFieldFilter?: boolean;
  /** `ChatProgress` の testId（`-summary` / `-current` / `-step-<id>` など）。囲みは `-process`、「処理の詳細」は `-details`。 */
  testId?: string;
  className?: string;
}) {
  const resolvedTrace = traceSummary ?? (diagnostics && typeof diagnostics === "object" && "trace_summary" in diagnostics ? diagnostics.trace_summary : undefined);
  const details = diagnostics ? (
    <AnswerDetailsPanel
      diagnostics={diagnostics}
      showNotices={showNotices}
      showAutoFieldFilter={showAutoFieldFilter}
    />
  ) : savedRecord ? (
    <SavedAnswerRecord
      traceId={savedRecord.traceId}
      searchAnswerProfileId={savedRecord.searchAnswerProfileId}
      showAnswer={false}
      showCitations={false}
    />
  ) : null;
  if (steps.length === 0 && !details && resolvedTrace == null) return null;
  // 実行の詳細の「処理の経過」の節と同じ共有の部品・名前（`RunProcessDetails`。#2325）。
  const detailsDisclosure = details || resolvedTrace != null ? (
    <RunProcessDetails
      traceSummary={resolvedTrace}
      traceLabels={createTraceDetailsLabels(t)}
      title={t("answer.process.details")}
      summaryTestId={testId ? `${testId}-details` : undefined}
    >
      {details}
    </RunProcessDetails>
  ) : null;
  return (
    <section
      className={className}
      aria-label={t("answer.process.title")}
      data-testid={testId ? `${testId}-process` : undefined}
    >
      {steps.length > 0 ? (
        <ChatProgress
          traceLabels={createTraceDetailsLabels(t)}
          steps={steps}
          active={active}
          startedAt={startedAt}
          elapsedMs={elapsedMs}
          reconnecting={reconnecting}
          progressKey={progressKey}
          footer={detailsDisclosure}
          testId={testId}
        />
      ) : (
        detailsDisclosure
      )}
    </section>
  );
}
