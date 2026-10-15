import { ChatProgress, Disclosure, type ChatProgressStep } from "@production-ready/ui";
import type { ReactNode } from "react";

import { t } from "@/lib/i18n";

/**
 * 回答（チャット・RAG 検索）の「処理の経過」（#2096）。3 製品共通の `ChatProgress`（工程の一覧。既定で開く・
 * 実行中は行が増え、今の行に Spinner と経過時間）と、その下の「詳細」（使ったモデル・回答の確認の結果・
 * 根拠の構成・開始・Trace などの技術的な詳細。既定で閉じる。messaging.md §10）を 1 か所にまとめる。
 *
 * チャットと RAG 検索で同じ部品・同じ文言を使う。完了した回答では、回答の本文 → 根拠の一覧 → この表示 →
 * 評価の順に並べる（page-archetypes.md §6.1）。
 */
export function AnswerProcess({
  steps,
  active,
  startedAt,
  elapsedMs,
  reconnecting,
  progressKey,
  details,
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
  /** 「詳細」の中身。無ければ「詳細」を出さない。 */
  details?: ReactNode;
  /** `ChatProgress` の testId（`-summary` / `-current` / `-step-<id>` など）。囲みは `-process`、「詳細」は `-details`。 */
  testId?: string;
  className?: string;
}) {
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
