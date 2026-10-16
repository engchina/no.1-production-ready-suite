import { ChatProgress, Disclosure, type ChatProgressStep } from "@production-ready/ui";

import { t } from "@/lib/i18n";
import { AnswerDetailsPanel } from "./AnswerDetailsPanel";
import { SavedAnswerRecord } from "./SavedAnswerRecord";

/**
 * 回答（チャット・RAG 検索）の「処理の経過」（#2096）。3 製品共通の `ChatProgress`（工程の一覧。実行中は開いて
 * 行が増え、今の行に Spinner と経過時間）と、その下の「詳細」（回答の記録の詳細。技術的な詳細は既定で閉じる。
 * messaging.md §10）を 1 か所にまとめる。
 *
 * チャットと RAG 検索は、この 1 つの部品で同じ位置（回答・検索結果の上。#2115）・同じ開閉・同じ「詳細」を出す
 * （#2118。チャットの形にそろえる）。工程の一覧は実行中は開き、完了したら閉じる（`collapseWhenDone`）。「詳細」は
 * 回答の記録の診断（`diagnostics`）があればその詳細（`traceId` があれば標準回答による評価も）、無ければ保存済みの
 * 回答の記録（`savedRecord`。会話を開き直したとき）を読み直して出す。
 */
export function AnswerProcess({
  steps,
  active,
  startedAt,
  elapsedMs,
  reconnecting,
  progressKey,
  diagnostics,
  traceId,
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
  /** 回答の記録の診断（`diagnostics.answer`）。完了後の「詳細」に出す。実行中は渡さない。 */
  diagnostics?: unknown;
  /** 保存した回答の trace ID（「詳細」の標準回答による評価）。回答を作らない検索は渡さない。 */
  traceId?: string | null;
  /** 診断の無い保存済みの回答（会話を開き直したとき）。回答の記録を読み直して「詳細」に出す。 */
  savedRecord?: { traceId: string; searchAnswerProfileId: string } | null;
  /** 回答を読むときに要る案内を「詳細」に出すか。回答の本文の下に出すときは false（既定）。 */
  showNotices?: boolean;
  /** 質問から読み取った条件を「詳細」に出すか。RAG 検索は外せるチップで出すので出さない。 */
  showAutoFieldFilter?: boolean;
  /** `ChatProgress` の testId（`-summary` / `-current` / `-step-<id>` など）。囲みは `-process`、「詳細」は `-details`。 */
  testId?: string;
  className?: string;
}) {
  const details = diagnostics ? (
    <AnswerDetailsPanel
      diagnostics={diagnostics}
      traceId={traceId}
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
  if (steps.length === 0 && !details) return null;
  return (
    <section
      className={className}
      aria-label={t("answer.process.title")}
      data-testid={testId ? `${testId}-process` : undefined}
    >
      <ChatProgress
        steps={steps}
        active={active}
        startedAt={startedAt}
        elapsedMs={elapsedMs}
        reconnecting={reconnecting}
        progressKey={progressKey}
        collapseWhenDone
        testId={testId}
      />
      {details ? (
        <Disclosure
          variant="plain"
          size="sm"
          summary={t("answer.process.details")}
          summaryProps={{ "data-testid": testId ? `${testId}-details` : undefined }}
          summaryClassName="text-fg-muted"
          className="mt-1"
        >
          {details}
        </Disclosure>
      ) : null}
    </section>
  );
}
