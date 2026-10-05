import { useEffect, useMemo, type ComponentProps } from "react";
import { Link } from "react-router-dom";
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiErrorBanner,
  Banner,
  ChatResultTable,
  ProcessingIndicator,
  TableSkeleton,
  TimedLoadingState,
  toast,
  type ButtonLinkComponent,
  type ChatResultTableLabels,
} from "@engchina/production-ready-ui";

import { useWorkspaceIdentity } from "@/components/WorkspaceState";
import { apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { APP_ROUTES } from "@/lib/routes";
import {
  chatResultCsvFilename,
  directSqlPrefillState,
  isIncompleteResult,
  lastExecutionText,
  toChatResultTable,
} from "../chatSqlExecution";
import type { JobData, SqlChatExecuteData } from "../types";
import { JobFailureBody } from "./JobFailureBody";

const executedAtFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/** 共通の結果の表の文言のうち、NULL の表示は SQL 生成の画面の結果の表と同じ辞書の値にする。 */
const RESULT_TABLE_LABELS: Partial<ChatResultTableLabels> = {
  nullValue: t("queryResults.null"),
};

export interface ChatSqlExecution {
  /**
   * この画面で受け取った結果（行を持つ）。送信のジョブの中で実行した結果（#1176）か、「もう一度実行」の結果。
   * 会話を開き直したときは無い（行は会話に保存しない）。
   */
  data: SqlChatExecuteData | undefined;
  running: boolean;
  /** 送信のジョブの中で実行した結果の行を受け取っている（#1176）。 */
  receiving: boolean;
  /** 実行の要求そのものの失敗（権限・通信・timeout）。SQL の実行の失敗は `data.status === "error"`。 */
  error: unknown;
  run: () => void;
}

/** 受け取りを始めたターン（再描画・作り直しで二重に受け取らない。行は 1 回だけ受け取れる）。 */
const receivedResults = new Set<string>();

/**
 * チャットのターンの SQL の実行（#1154）。結果は TanStack Query のキャッシュ（メモリ）に置き、会話を
 * 切り替えて戻っても同じ画面の中では出し直す。sessionStorage などには保存しない（業務データの行を残さない）。
 *
 * 送信のジョブの中で実行した結果（#1176）は、受け取りの期限（`resultExpiresAt`）の前に 1 回だけ受け取り、
 * 同じキャッシュに置く（backend は受け取ったら行を消す）。受け取れなかった（期限切れ・受け取り済み）ときは
 * 要約と「もう一度実行」。
 */
export function useChatSqlExecution(
  jobId: string,
  resultExpiresAt?: string | null,
): ChatSqlExecution {
  const identity = useWorkspaceIdentity();
  const queryClient = useQueryClient();
  const key = useMemo(
    () => ["nl2sql", "chat-execution", identity.owner, identity.context, jobId] as const,
    [identity.owner, identity.context, jobId],
  );
  const cached = useQuery<SqlChatExecuteData | null>({
    queryKey: key,
    queryFn: () => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  });
  const mutation = useMutation({
    mutationKey: key,
    mutationFn: () =>
      apiPost<SqlChatExecuteData>(
        `/api/nl2sql/jobs/${encodeURIComponent(jobId)}/execute`,
        {},
        { timeoutMs: API_TIMEOUT_MS.sqlExecute },
      ),
    onSuccess: (data) => queryClient.setQueryData(key, data),
  });
  const running = useIsMutating({ mutationKey: key, exact: true }) > 0;
  const receiveKey = useMemo(() => [...key, "receive"] as const, [key]);
  const received = useMutation({
    mutationKey: receiveKey,
    mutationFn: () =>
      apiPost<SqlChatExecuteData>(
        `/api/nl2sql/jobs/${encodeURIComponent(jobId)}/execution-result`,
        {},
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail },
      ),
    // 「もう一度実行」の結果が先に届いていれば、そちらを残す。
    onSuccess: (data) =>
      queryClient.setQueryData<SqlChatExecuteData | null>(key, (current) => current ?? data),
  });
  const receiving = useIsMutating({ mutationKey: receiveKey }) > 0;
  const receiveId = key.join("/");
  const hasData = Boolean(cached.data);
  const startReceive = received.mutate;
  useEffect(() => {
    if (!resultExpiresAt || hasData || receivedResults.has(receiveId)) return;
    // 期限を過ぎた行は backend が受け取らせない（要約と「もう一度実行」を出す）。
    if (!(Date.parse(resultExpiresAt) > Date.now())) return;
    receivedResults.add(receiveId);
    startReceive();
  }, [resultExpiresAt, hasData, receiveId, startReceive]);
  return {
    data: cached.data ?? undefined,
    running,
    receiving: receiving && !hasData,
    error: mutation.isError ? mutation.error : null,
    run: () => {
      if (!running) mutation.mutate();
    },
  };
}

/** 「SELECT SQL を実行」へ SQL を履歴の state で渡すリンク（URL には SQL を載せない）。 */
function directSqlLink(sql: string): ButtonLinkComponent {
  return function DirectSqlLink(props: ComponentProps<ButtonLinkComponent>) {
    return <Link {...props} state={directSqlPrefillState(sql)} />;
  };
}

/**
 * チャットの回答の吹き出しの中の実行結果（SQL の下）。実行中は経過時間、失敗は danger の Banner
 * （技術的な詳細は「詳細」）、成功は共通の `ChatResultTable`（要約・先頭の行のプレビュー・打ち切りの明示・
 * すべての行・CSV）。会話を開き直したときは前回の実行の要約だけを出す（行は保存しない）。
 */
export function ChatSqlExecutionResult({
  turn,
  execution,
  canOpenDirectSql,
}: {
  turn: JobData;
  execution: ChatSqlExecution;
  canOpenDirectSql: boolean;
}) {
  const { data, running, receiving, error } = execution;
  const sql = turn.result?.generated_sql ?? "";
  const DirectSqlLink = useMemo(() => directSqlLink(sql), [sql]);
  const table = useMemo(
    () => (data?.status === "done" ? toChatResultTable(data.results) : null),
    [data],
  );
  const lastExecution = turn.last_execution;
  if (!running && !receiving && !error && !data && !lastExecution) return null;

  return (
    <section
      aria-label={t("chat.execute.region")}
      className="min-w-0 space-y-3 border-t border-border pt-3"
      data-testid="sql-chat-execution"
    >
      {running ? (
        // スピナーは押した「実行」のボタンが出す（同じ処理のスピナーは 1 つ。messaging.md §3.7）。
        // 再実行の間は前の結果を残し、その上に出す。
        <ProcessingIndicator
          active
          label={t("chat.execute.running")}
          operationKey={`sql-chat-execute-${turn.job_id}`}
          placement="result"
          activityIcon="none"
          testId="sql-chat-execution-running"
        />
      ) : null}
      {receiving && !running ? (
        // 送信のジョブの中で実行した結果の行を受け取っている（#1176）。表の形の Skeleton で寸法を予約する。
        <TimedLoadingState
          label={t("chat.execute.receiving")}
          operationKey={`sql-chat-receive-${turn.job_id}`}
          framed={false}
          testId="sql-chat-execution-receiving"
        >
          <TableSkeleton rows={3} columns={3} />
        </TimedLoadingState>
      ) : null}
      {!running && error ? (
        <ApiErrorBanner
          error={error}
          fallback={t("chat.execute.failed")}
          testId="sql-chat-execution-request-error"
        />
      ) : null}
      {data?.status === "error" && !error ? (
        <div data-testid="sql-chat-execution-error">
          <Banner severity="danger">
            <JobFailureBody
              message={data.error_message || t("chat.execute.failed")}
              errorCode={data.error_code}
              errorDetail={data.error_detail}
            />
          </Banner>
        </div>
      ) : null}
      {data?.status === "done" && table && !error ? (
        <ChatResultTable
          columns={table.columns}
          rows={table.rows}
          truncated={isIncompleteResult(data.results)}
          rowLimit={data.row_limit}
          cellsTruncated={data.cells_truncated}
          maxCellChars={data.max_cell_chars}
          elapsedMs={data.elapsed_ms}
          csvFilename={chatResultCsvFilename(data.executed_at)}
          onCsvDownloaded={() => toast.success(t("common.action.downloaded"))}
          fullResult={
            canOpenDirectSql
              ? {
                  href: APP_ROUTES.directSql,
                  label: t("chat.execute.openDirectSql"),
                  linkComponent: DirectSqlLink,
                  hint: t("chat.execute.fullResultHint"),
                }
              : { hint: t("chat.execute.fullResultHintNoLink") }
          }
          labels={RESULT_TABLE_LABELS}
          testId="sql-chat-result"
        />
      ) : null}
      {!running && !receiving && !error && !data && lastExecution ? (
        <p className="text-sm text-fg-muted" data-testid="sql-chat-last-execution">
          {lastExecutionText(
            lastExecution,
            executedAtFormatter.format(new Date(lastExecution.executed_at)),
          )}
        </p>
      ) : null}
    </section>
  );
}
