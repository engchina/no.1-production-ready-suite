import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  Check,
  RefreshCw,
  RotateCcw,
  Square,
  Wrench,
  X,
} from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  ChatComposer,
  ChatHistoryList,
  ChatLayout,
  ChatProgress,
  ChatSkeleton,
  useChatProgressTracker,
  ChatAnswer,
  ChatPendingTurn,
  ChatTurn,
  Disclosure,
  EmptyState,
  MessageText,
  PageBody,
  PageHeader,
  SearchableSelectField,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  createOptimisticChatMessage,
  toast,
  withOptimisticChatStatus,
  type OptimisticChatMessage,
  useChatAutoScroll,
  useChatHistoryPanel,
  ApiErrorBanner,
  apiErrorMessage,
  DEFAULT_PAGE_SIZE,
  offsetAfterShrink,
  offsetForPage,
  offsetPagination,
} from "@engchina/production-ready-ui";

import {
  ApiError,
  agentApi,
  type ApprovalRequest,
  type Artifact,
  type RunState,
  type RunStep,
  type ThreadData,
  type ThreadSummary,
} from "@/lib/api";
import { isRunnableAgent } from "@/lib/agent-availability";
import { chatSubmitProgressSteps, runProgressSteps } from "@/lib/chat-progress";
import { AnswerFeedback } from "@/components/chat/AnswerFeedback";
import { agentPaginationLabels } from "@/components/ListViews";
import { AnswerBody, ToolResultTable } from "@/components/chat/ResultTables";
import { useAuth } from "@/components/security/AuthProvider";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { answerSources, type RagEvidenceView } from "@/lib/rag-evidence";
import { runToolResultTables } from "@/lib/run-tables";
import { runStatusView, stepStatusView } from "@/lib/status-labels";
import { isNullableString, isString, useWorkspaceState } from "@/lib/workspace-state";
import { isOwnDecision } from "@/pages/shared/approval-decision";

/**
 * 業務利用者のチャット（#768）。使ってよい業務 Agent を選んで会話する。1 往復が 1 Run で、
 * 同じ会話の前の質問と回答は組み込み Runtime がモデルへ渡す。回答の下に出典・使ったツール・
 * 承認待ちを出す。画面の型は RAG と同じ（会話は既定で全幅、履歴は必要なときに開く）。
 */

const ACTIVE_STATUSES = new Set<RunState["status"]>(["queued", "running"]);
/** 「停止」で止められる Run（回答の作成中と、ツールの承認待ち。#805）。 */
const STOPPABLE_STATUSES = new Set<RunState["status"]>(["queued", "running", "waiting_approval"]);
const POLL_INTERVAL_MS = 1500;
/**
 * 会話の取り直しが成功しないまま、この時間が過ぎたら取り直し直す（#1160）。取り直しは
 * {@link POLL_INTERVAL_MS} ごとなので、その数回分。応答しない取得を打ち切って新しく取り直す。
 */
const CHAT_PROGRESS_STALE_AFTER_MS = 10_000;
/** 会話の 1 回の取得の上限（応答が返らない取得で取り直しを止めない。#1160）。 */
const THREAD_FETCH_TIMEOUT_MS = 30_000;

/** 会話の履歴のページ（どの Agent の一覧の offset か）。 */
interface HistoryPage {
  agentId: string;
  offset: number;
}

function isHistoryPage(value: unknown): value is HistoryPage {
  const page = value as HistoryPage;
  return (
    typeof page === "object" &&
    page !== null &&
    typeof page.agentId === "string" &&
    Number.isInteger(page.offset) &&
    page.offset >= 0
  );
}

export function ChatPage() {
  const queryClient = useQueryClient();
  const capabilities = useCapabilities();
  const { user } = useAuth();
  // 会話の履歴は既定で閉じ、チャットを全幅にする（RAG のチャットと同じ型。#664 / #889）。
  // lg 以上のインラインのパネルの開閉は作業状態に残す。lg 未満のモーダルの side sheet は残さない
  // （戻ったとき・再読込で画面を塞がない。workspace-state.md）。
  // 開閉の判定・シートの開閉は共通の useChatHistoryPanel（3 製品共通。#1161）。
  const [historyPanelOpen, setHistoryPanelOpen] = useWorkspaceState("chat", "historyOpen", false);
  const history = useChatHistoryPanel({
    inlineOpen: historyPanelOpen,
    onInlineOpenChange: setHistoryPanelOpen,
  });
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const [agentId, setAgentId] = useWorkspaceState("chat", "agentId", "", isString);
  const [threadId, setThreadId] = useWorkspaceState<"chat", string | null>(
    "chat",
    "threadId",
    null,
    isNullableString
  );
  const [draft, setDraft] = useWorkspaceState("chat", "draft", "", isString);
  // 送った質問（#907）。Run の作成の応答を待たずに会話の欄の末尾へ出し、作られた Run に置き換える。
  // 作れなかったときは残して「再送信」を出す（入力を失わない）。
  const [pending, setPending] = useState<OptimisticChatMessage | null>(null);

  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const usableAgents = useMemo(
    () => (agents.data?.agents ?? []).filter(isRunnableAgent),
    [agents.data]
  );
  // 選んだ Agent が使えなくなっていたら、先頭の Agent にする。
  const selectedAgent = usableAgents.find((agent) => agent.id === agentId) ?? usableAgents[0];
  const selectedAgentId = selectedAgent?.id ?? "";

  // 会話の履歴はサーバー側でページングする（3 製品共通。#1265）。ページは作業状態に残し、別の Agent に移ったら 1 ページ目から。
  const [historyPage, setHistoryPage] = useWorkspaceState<"chat", HistoryPage>(
    "chat",
    "historyPage",
    { agentId: "", offset: 0 },
    isHistoryPage
  );
  const threadsOffset = historyPage.agentId === selectedAgentId ? historyPage.offset : 0;
  const setThreadsOffset = (offset: number) => setHistoryPage({ agentId: selectedAgentId, offset });
  const threads = useQuery({
    queryKey: ["threads", selectedAgentId, threadsOffset],
    queryFn: () => agentApi.listThreads(selectedAgentId, { limit: DEFAULT_PAGE_SIZE, offset: threadsOffset }),
    enabled: Boolean(selectedAgentId),
    // 次のページを取得している間は、表示中のページを出したままにする。
    placeholderData: keepPreviousData,
  });
  // 会話が減って今のページが空になったら、最後のページへ戻す（空の案内を出さない）。
  const shrunkThreadsOffset =
    threads.data && threads.data.offset === threadsOffset
      ? offsetAfterShrink({
          offset: threadsOffset,
          limit: DEFAULT_PAGE_SIZE,
          total: threads.data.total,
          count: threads.data.threads.length,
        })
      : null;
  if (shrunkThreadsOffset !== null) {
    setThreadsOffset(shrunkThreadsOffset);
  }
  const thread = useQuery({
    queryKey: ["thread", threadId],
    queryFn: ({ signal }) =>
      agentApi.getThread(threadId ?? "", {
        signal: AbortSignal.any([signal, AbortSignal.timeout(THREAD_FETCH_TIMEOUT_MS)]),
      }),
    enabled: Boolean(threadId),
    retry: false,
    // 実行中の Run があるあいだだけ取り直す（終わったら止める）。
    refetchInterval: (query) =>
      (query.state.data?.runs ?? []).some((run) => ACTIVE_STATUSES.has(run.status))
        ? POLL_INTERVAL_MS
        : false,
  });
  // 選んでいた会話が消えた（404）・読めなくなった（403）・別の Agent の会話なら、新しい会話に戻す。
  // 通信断・timeout・5xx などの一時的な失敗では戻さない（回答の作成中の取り直しが 1 回失敗しただけで、
  // 会話が消えて新しい会話になっていた）。読めていた会話はそのまま出し、取り直しを続ける。
  const threadMissing = Boolean(threadId) && thread.isError && isThreadGone(thread.error);
  const threadOfOtherAgent =
    thread.data !== undefined && selectedAgentId !== "" && thread.data.agent_id !== selectedAgentId;
  useEffect(() => {
    if (threadMissing || threadOfOtherAgent) setThreadId(null);
  }, [threadMissing, threadOfOtherAgent, setThreadId]);

  // 画面の前提（業務 Agent の一覧・開いている会話の内容）が揃うまでは、会話の欄に空の状態を出さず
  // 会話の形の Skeleton で覆い、送信を止める（messaging.md §11.7、#1153）。入力欄・新しい会話・履歴の開閉を
  // 無効にするのは業務 Agent の一覧の読み込み中だけ。会話の内容の読み込み中は入力欄に書ける
  // （書いている途中で無効にしてフォーカスと入力を失わせない。#1188）。
  // 会話の内容を読めなかった（一時的な失敗）ときも送信しない（内容の分からない会話に続けて送らない）。
  const agentsLoading = agents.isLoading;
  const threadLoading = Boolean(threadId) && thread.isLoading;
  const threadFailed = Boolean(threadId) && thread.isError && !thread.data && !threadMissing;
  const prerequisitesLoading = agentsLoading || threadLoading;
  const runs = threadId && !threadOfOtherAgent ? (thread.data?.runs ?? []) : [];
  // 処理の経過の取り直し（#1160）。応答しない取得（取得中は interval が次を始めない）を打ち切って取り直す。
  const refetchThread = thread.refetch;
  const refreshThread = () => refetchThread({ cancelRefetch: true, throwOnError: false });
  const running = runs.some((run) => ACTIVE_STATUSES.has(run.status));
  const waitingApproval = runs.some((run) => run.status === "waiting_approval");
  const stoppableRun = [...runs].reverse().find((run) => STOPPABLE_STATUSES.has(run.status));
  const lastRunId = runs.at(-1)?.id;
  const lastRunStatus = runs.at(-1)?.status;

  // 会話の欄の自動スクロール（3 製品共通の useChatAutoScroll。#1161）。祖先（ページ・document）は動かさない。
  // 末尾を見ている間は新しい Run・状態の変化に合わせて末尾へ追い、上を読んでいる間は「最新へ」を出す。
  const autoScroll = useChatAutoScroll({
    contentKey: `${lastRunId ?? ""}:${lastRunStatus ?? ""}:${runs.at(-1)?.steps.length ?? 0}:${pending?.localId ?? ""}:${pending?.status ?? ""}`,
    resetKey: `${selectedAgentId}:${threadId ?? ""}`,
  });

  // 評価を保存した Run を、会話の取り直しを待たずに差し替える（#774）。
  function replaceRun(updated: RunState) {
    queryClient.setQueryData<ThreadData>(["thread", threadId], (current) =>
      current
        ? { ...current, runs: current.runs.map((item) => (item.id === updated.id ? updated : item)) }
        : current
    );
  }

  // 送信の要求中に「停止」を押したら、Run ができた直後に止める（Run の id は応答で分かる。#805）。
  const stopAfterCreateRef = useRef(false);
  const cancel = useMutation({
    mutationFn: (runId: string) => agentApi.cancelRun(runId),
    onSuccess: (updated) => {
      // 取り直しを待たずに止めた状態を出し、ボタンを「送信」に戻す。
      replaceRun(updated);
      void queryClient.invalidateQueries({ queryKey: ["thread", updated.thread_id ?? threadId] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });

  const send = useMutation({
    mutationFn: (goal: string) =>
      agentApi.createRun({
        goal,
        agent_id: selectedAgentId,
        thread_id: threadId ?? undefined,
      }),
    onSuccess: (run) => {
      if (run.thread_id) {
        // 作られた Run を会話に入れてから、送った質問（仮）を外す。取り直しを待たず、二重にも出さない（#907）。
        const createdThreadId = run.thread_id;
        queryClient.setQueryData<ThreadData>(["thread", createdThreadId], (current) => ({
          thread_id: createdThreadId,
          agent_id: current?.agent_id ?? run.agent_id,
          runs: [...(current?.runs ?? []).filter((item) => item.id !== run.id), run],
        }));
        setThreadId(createdThreadId);
      }
      setPending(null);
      void queryClient.invalidateQueries({ queryKey: ["thread", run.thread_id] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
      if (stopAfterCreateRef.current) {
        stopAfterCreateRef.current = false;
        cancel.mutate(run.id);
      }
    },
    onError: () => {
      stopAfterCreateRef.current = false;
      // 送れなかった質問は会話の欄に残し、理由と「再送信」を出す（入力欄には戻さない。#907）。
      setPending((current) => (current ? withOptimisticChatStatus(current, "failed") : current));
    },
  });

  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      agentApi.decideApproval(approval.id, { approved }),
    onSuccess: (updatedRun, { approval, approved }) => {
      // 押した判断が自分の判断として残ったときだけ成功と案内する。ほかの操作者が先に判断した・実行が先に
      // 終わった承認は、backend が状態を変えずに 200 で返す（実行履歴・承認の画面と同じ。#1119 / #1138）。
      if (isOwnDecision(updatedRun, approval.id, approved, user?.login_user_id)) {
        toast.success(approved ? t("chat.approval.approved") : t("chat.approval.rejected"));
      } else {
        toast.info(t("approval.changedDuringReview"));
      }
      void queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
    },
    onError: (error) => toast.error(apiErrorMessage(error, t("common.error.operation"))),
  });

  function submit() {
    const goal = draft.trim();
    // 実行中・承認待ちのあいだは次の質問を送らない（前の回答を会話の履歴に含めるため）。
    // 実行中の Enter でも停止しない（停止はボタンだけ。buttons.md §3.1）。
    if (
      !goal ||
      !selectedAgentId ||
      running ||
      waitingApproval ||
      send.isPending ||
      prerequisitesLoading ||
      threadFailed
    )
      return;
    // 送った質問はすぐ会話の欄に出し、入力欄を空にする。回答の作成中も次の質問を書ける（#907）。
    setPending(createOptimisticChatMessage(goal));
    setDraft("");
    // 送った質問は会話の欄の末尾に出す。上を読んでいても末尾へ戻る（messaging.md §11.1）。
    autoScroll.scrollToLatest();
    cancel.reset();
    send.mutate(goal);
  }

  /** 送れなかった質問を、そのままもう一度送る（#907）。 */
  function resend() {
    if (!pending || !selectedAgentId || running || waitingApproval || send.isPending) return;
    setPending(withOptimisticChatStatus(pending, "sending"));
    cancel.reset();
    send.mutate(pending.content);
  }

  function stop() {
    if (cancel.isPending) return;
    if (send.isPending) {
      stopAfterCreateRef.current = true;
      return;
    }
    if (stoppableRun) cancel.mutate(stoppableRun.id);
  }

  function startNewThread() {
    setThreadId(null);
    setPending(null);
    send.reset();
    cancel.reset();
    // 新しい会話はすぐ書き始められるよう、入力欄へフォーカスする（RAG と同じ）。
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  function openThread(item: ThreadSummary) {
    // lg 未満のシートは、会話を選んだら閉じる（SideSheet が開閉ボタンへフォーカスを戻す）。
    history.closeSheet();
    setThreadId(item.thread_id);
    setPending(null);
    send.reset();
    cancel.reset();
  }

  // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す（RAG と同じ。#664）。
  const currentThreadId = threadOfOtherAgent ? null : threadId;
  const currentThreadTitle = currentThreadId
    ? (threads.data?.threads.find((item) => item.thread_id === currentThreadId)?.title ?? runs[0]?.goal)
    : undefined;

  // 一覧の行・読み込み中・失敗・0 件は 3 製品共通の ChatHistoryList（#1161）。
  const historyContent = (
    <ChatHistoryList
      items={(threads.data?.threads ?? []).map((item) => ({
        id: item.thread_id,
        title: item.title,
        meta: `${formatTime(item.updated_at)}・${t("chat.threads.turns", { count: item.run_count })}`,
        badge:
          item.last_status === "completed" ? undefined : (
            <StatusBadge variant={runStatusView(item.last_status).variant} label={t(`chat.status.${item.last_status}`)} />
          ),
      }))}
      currentId={currentThreadId}
      onSelect={(item) => {
        const thread = threads.data?.threads.find((candidate) => candidate.thread_id === item.id);
        if (thread) openThread(thread);
      }}
      // 業務 Agent の一覧の読み込み中は会話の一覧をまだ取得できない。一覧の形だけを出す（#1153）。
      waiting={agentsLoading}
      loading={threads.isLoading}
      error={threads.isError && !threads.data ? threads.error : null}
      onRetry={() => void threads.refetch()}
      retrying={threads.isFetching}
      labels={{
        list: t("chat.threads.title"),
        loading: t("chat.threads.loading"),
        error: t("chat.threads.loadFailed"),
        retry: t("common.retry"),
        empty: t("chat.threads.empty"),
      }}
      testIds={{
        skeleton: "chat-threads-skeleton",
        loading: "chat-threads-loading",
        error: "chat-threads-error",
        list: "chat-thread-list",
        pagination: "chat-threads-pagination",
      }}
      pagination={
        threads.data && threads.data.threads.length > 0
          ? {
              ...offsetPagination({
                offset: threads.data.offset,
                limit: DEFAULT_PAGE_SIZE,
                total: threads.data.total,
                count: threads.data.threads.length,
              }),
              onPageChange: (next) => setThreadsOffset(offsetForPage(next, DEFAULT_PAGE_SIZE)),
              labels: { ...agentPaginationLabels(), ariaLabel: t("chat.threads.pagination") },
            }
          : undefined
      }
    />
  );

  const composerBlocked = running || waitingApproval;
  // 送信と停止は同じボタン。送信の要求中・回答の作成中・承認待ちは同じ位置で「停止」になる（buttons.md §3.1、#805）。
  const stoppable = send.isPending || Boolean(stoppableRun);

  return (
    <div className="flex min-h-full shrink-0 flex-col lg:h-full lg:min-h-0">
      <PageHeader wide title={t("chat.title")} subtitle={t("chat.subtitle")} />
      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4">
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {agents.isLoading ? (
              <TimedLoadingState label={t("chat.agent.loading")} framed={false} testId="chat-agents-loading">
                {/* ラベルと欄の寸法を予約する（RAG・NL2SQL の対象の欄と同じ形。#1153）。 */}
                <div className="space-y-1.5" aria-hidden="true">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-[var(--button-height-md)] w-full" />
                </div>
              </TimedLoadingState>
            ) : agents.isError ? (
              // 読めなかったときは会話の欄を出さず（空の状態を出さない）、ここに失敗と再試行を出す（#1153）。
              <ApiErrorBanner
                error={agents.error}
                fallback={t("common.error.load")}
                testId="chat-agents-error"
                action={
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={RefreshCw}
                    loading={agents.isFetching}
                    onClick={() => void agents.refetch()}
                  >
                    {t("common.retry")}
                  </Button>
                }
              />
            ) : usableAgents.length === 0 ? (
              <EmptyState title={t("chat.agent.empty")} />
            ) : (
              // 画面の対象を決める主な選択欄（RAG の検索・回答プロファイルと同じく full）。
              <SearchableSelectField
                id="chat-agent"
                label={t("chat.agent.label")}
                value={selectedAgentId}
                options={usableAgents.map((agent) => ({
                  value: agent.id,
                  label: agent.name,
                  description: agent.description || undefined,
                }))}
                onValueChange={(value) => {
                  setAgentId(value);
                  setThreadId(null);
                  setPending(null);
                  send.reset();
                }}
                width="full"
              />
            )}
          </CardContent>
        </Card>

        {/* 業務 Agent の読み込み中も会話の領域を描く（寸法を予約し、読み込み後に現れて押し下げない。#1153）。 */}
        {agentsLoading || selectedAgentId ? (
          // 骨格（履歴のパネル・シート、会話の領域の上端の行・会話の欄・入力欄の領域）は 3 製品共通の ChatLayout（#1161）。
          <ChatLayout
            history={history}
            historyTitle={t("chat.threads.title")}
            historyCloseLabel={t("chat.threads.close")}
            historyContent={historyContent}
            historyToggleDisabled={agentsLoading}
            label={t("chat.conversation")}
            // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す（RAG と同じ。#664）。
            conversationTitle={currentThreadId ? currentThreadTitle : null}
            conversationTitleLoading={Boolean(currentThreadId)}
            newConversation={{ label: t("chat.threads.new"), onClick: startNewThread, disabled: agentsLoading }}
            logLabel={t("chat.messages")}
            logRef={autoScroll.logRef}
            latest={{
              visible: autoScroll.showLatest,
              label: t("chat.latest"),
              onClick: () => autoScroll.scrollToLatest("smooth"),
            }}
            composer={
              <>
                {/* 入力欄の領域（入力欄と送信 / 停止）は 3 製品共通の ChatComposer（#1161）。送信と停止は同じボタンで、
                    送信の要求中・回答の作成中・承認待ちは同じ位置で「停止」になる（buttons.md §3.1、#805）。
                    IME の変換を確定する Enter では送信しない（#459）。Shift+Enter は改行。
                    業務 Agent の一覧の読み込み中は書けない（書いた文字は作業状態に残す。#1153）。
                    会話の内容の読み込み中は書けて、送信だけを止める（#1188）。 */}
                <ChatComposer
                  id="chat-composer"
                  textareaRef={composerRef}
                  value={draft}
                  onValueChange={setDraft}
                  onSubmit={submit}
                  onStop={stop}
                  running={stoppable}
                  submitBlocked={composerBlocked || threadLoading || threadFailed}
                  disabled={agentsLoading}
                  label={t("chat.composer.label")}
                  placeholder={t("chat.composer.placeholder")}
                  sendLabel={t("chat.send")}
                  stopLabel={t("chat.stop")}
                  sendTestId="chat-send"
                  footer={
                    cancel.error ? (
                      // 停止の失敗は入力欄の下に出す（3 製品で同じ ApiErrorBanner。#1161）。
                      <ApiErrorBanner error={cancel.error} fallback={t("chat.stopFailed")} testId="chat-stop-error" />
                    ) : null
                  }
                />
              </>
            }
          >
            {threadLoading && !pending ? (
              // 会話の内容の読み込み中は文言と経過時間を出し、会話の形の Skeleton で寸法を予約する（3 製品で同じ。#1153）。
              <TimedLoadingState label={t("chat.loading")} framed={false} testId="chat-thread-loading">
                <ChatSkeleton />
              </TimedLoadingState>
            ) : agentsLoading ? (
              // 業務 Agent の一覧の読み込み中は空の状態を出さず、会話の形の Skeleton だけを出す。
              // 経過時間は上のカードが出しているので重ねない（同じ取得の経過時間は 1 か所。#1153）。
              <ChatSkeleton testId="chat-conversation-skeleton" />
            ) : threadFailed ? (
              // 会話を読めなかった（一時的な失敗）。選んだ会話のまま、理由と再試行を出す。
              <ApiErrorBanner
                error={thread.error}
                fallback={t("chat.loadFailed")}
                testId="chat-thread-error"
                action={
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={RefreshCw}
                    loading={thread.isFetching}
                    onClick={() => void thread.refetch()}
                  >
                    {t("common.retry")}
                  </Button>
                }
              />
            ) : runs.length === 0 && !pending ? (
              <EmptyState title={t("chat.empty.title")} hint={t("chat.empty.hint")} />
            ) : (
              runs.map((run) => (
                <RunChatTurn
                  key={run.id}
                  run={run}
                  canDecide={capabilities.decideApprovals}
                  // 評価は会話をした本人だけ（会話の一覧は本人の会話だけ。backend も作成者を確かめる。#774）。
                  canRate={capabilities.operateRuns}
                  deciding={decide.isPending}
                  onDecide={(approval, approved) => decide.mutate({ approval, approved })}
                  onFeedbackSaved={replaceRun}
                  receivedAt={thread.dataUpdatedAt}
                  refresh={refreshThread}
                />
              ))
            )}
            {pending ? (
              <PendingTurn
                message={pending}
                errorMessage={send.error ? apiErrorMessage(send.error, t("chat.failedUnknown")) : null}
                resendDisabled={composerBlocked || send.isPending}
                onResend={resend}
              />
            ) : null}
          </ChatLayout>
        ) : null}
      </PageBody>
    </div>
  );
}

function RunChatTurn({
  run,
  canDecide,
  canRate,
  deciding,
  onDecide,
  onFeedbackSaved,
  receivedAt,
  refresh,
}: {
  run: RunState;
  canDecide: boolean;
  canRate: boolean;
  deciding: boolean;
  onDecide: (approval: ApprovalRequest, approved: boolean) => void;
  onFeedbackSaved: (run: RunState) => void;
  /** 会話を最後に取得できた時刻（TanStack Query の dataUpdatedAt）。 */
  receivedAt: number;
  /** 会話を取り直す（応答しない取得は打ち切る）。 */
  refresh: () => Promise<unknown>;
}) {
  const answer = answerText(run.artifacts);
  const citations = runCitations(run.artifacts);
  const toolSteps = run.steps.filter((step) => step.tool_call);
  const toolTables = runToolResultTables(run);
  const pendingApprovals = run.approvals.filter((approval) => approval.status === "pending");
  const failure = run.status === "failed" ? failureMessage(run) : null;
  // 処理の経過の状態を追う（3 製品共通。#1160）。回答の作成中（取り直している間）に会話の取得が途絶えたら
  // 取り直し、終端まで追う。承認待ちは利用者の判断を待つ間なので取り直さない。
  const progress = useChatProgressTracker({
    key: run.id,
    steps: runProgressSteps(run),
    active: STOPPABLE_STATUSES.has(run.status),
    receivedAt,
    refresh,
    staleAfterMs: CHAT_PROGRESS_STALE_AFTER_MS,
    enabled: ACTIVE_STATUSES.has(run.status),
  });

  return (
    // 1 往復の入れ物（質問の吹き出し・回答の枠）は 3 製品共通の ChatTurn / ChatAnswer（#1161）。
    <ChatTurn question={run.goal} testId={`chat-turn-${run.id}`}>
      <ChatAnswer live>
        {/*
          処理の段階（考えている・ツールの呼び出し・承認待ち・回答の作成。#1147）。Run の取り直しで更新し、
          完了後は回答の上に「処理の経過」の 1 行に畳む（共有の ChatProgress。3 製品で同じ。#1145）。
        */}
        {/* 経過時間は Run の作成（送信の受付）から数え続ける（段階ごとに 0 に戻さない。#1176）。 */}
        <ChatProgress {...progress.progressProps} startedAt={run.created_at} testId="chat-progress" />
        {answer ? (
          // 回答の Markdown の表と、表の形のツールの結果（NL2SQL の SQL の実行の結果など）は、NL2SQL のチャットと
          // 同じ共通の結果の表で出す（#1158）。表でない部分・表でない結果は今までどおり。
          <AnswerBody
            text={answer}
            renderText={(text) => <MessageText text={text} className="block text-sm text-fg" />}
            testId={`chat-answer-${run.id}`}
          />
        ) : null}
        {toolTables.map((table) => (
          <ToolResultTable
            key={table.stepId}
            data={table.data}
            toolName={table.toolName}
            testId={`chat-tool-table-${table.stepId}`}
          />
        ))}
        {failure ? <Banner severity="danger" title={t("chat.failed")}>{failure}</Banner> : null}
        {run.status === "cancelled" ? (
          // 止めた回答の状態（途中までの回答は上に残す）。色だけに頼らず、停止のアイコンを添える（#805）。
          <p className="flex items-center gap-1.5 text-sm text-fg-muted" data-testid="chat-cancelled">
            <Square size={14} aria-hidden="true" />
            {t("chat.cancelled")}
          </p>
        ) : null}

        {pendingApprovals.map((approval) => (
          <Banner key={approval.id} severity="warning" title={t("chat.approval.title")}>
            <div className="space-y-2">
              <p className="break-words">
                {t("chat.approval.message", { tool: approval.tool_call.name })}
              </p>
              <pre className="max-h-40 overflow-auto rounded-sm bg-surface-sunken p-2 text-xs">
                {JSON.stringify(approval.tool_call.arguments ?? {}, null, 2)}
              </pre>
              {canDecide ? (
                <div className="flex flex-wrap gap-2">
                  <Button size="sm" icon={Check} disabled={deciding} onClick={() => onDecide(approval, true)}>
                    {t("chat.approval.approve")}
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={X}
                    disabled={deciding}
                    onClick={() => onDecide(approval, false)}
                  >
                    {t("chat.approval.reject")}
                  </Button>
                </div>
              ) : (
                <p className="text-xs">{t("chat.approval.waiting")}</p>
              )}
            </div>
          </Banner>
        ))}

        {citations.length > 0 ? (
          <Disclosure
            variant="plain"
            size="sm"
            summary={
              <span className="inline-flex items-center gap-1">
                <BookOpen size={14} aria-hidden="true" />
                {t("chat.sources", { count: citations.length })}
              </span>
            }
          >
            <ol className="space-y-2">
              {citations.map((citation, index) => (
                <li key={citation.key} className="rounded-sm bg-surface-sunken p-2 text-xs">
                  <p className="font-medium text-fg">
                    {index + 1}. {citation.title}
                  </p>
                  {citation.location ? (
                    <p className="mt-0.5 break-words text-fg-muted" data-testid="chat-source-location">
                      {citation.location}
                    </p>
                  ) : null}
                  {citation.text ? (
                    <p className="mt-1 line-clamp-3 break-words text-fg-muted">{citation.text}</p>
                  ) : null}
                </li>
              ))}
            </ol>
          </Disclosure>
        ) : null}

        {toolSteps.length > 0 ? (
          <Disclosure
            variant="plain"
            size="sm"
            summary={
              <span className="inline-flex items-center gap-1">
                <Wrench size={14} aria-hidden="true" />
                {t("chat.tools", { count: toolSteps.length })}
              </span>
            }
          >
            <ul className="space-y-1">
              {toolSteps.map((step) => (
                <ToolStepRow key={step.id} step={step} />
              ))}
            </ul>
          </Disclosure>
        ) : null}

        {canRate && run.status === "completed" && answer ? (
          <AnswerFeedback runId={run.id} current={run.feedback ?? null} onSaved={onFeedbackSaved} />
        ) : null}
      </ChatAnswer>
    </ChatTurn>
  );
}

/**
 * 送った質問（Run の作成の応答の前・作れなかったとき。#907）。作成中の表示は Run の回答の場所と同じ形にし、
 * Run ができたら同じ位置の `ChatTurn` に置き換わる。作れなかったときは質問を残し、理由と「再送信」を出す。
 */
function PendingTurn({
  message,
  errorMessage,
  resendDisabled,
  onResend,
}: {
  message: OptimisticChatMessage;
  errorMessage: string | null;
  resendDisabled: boolean;
  onResend: () => void;
}) {
  return (
    // 1 往復の入れ物・送信中の回答の場所・失敗の出し方は 3 製品共通の ChatPendingTurn（#1161）。
    <ChatPendingTurn
      message={message}
      failedLabel={t("chat.sendFailed")}
      testId="chat-pending-turn"
      // Run の作成の応答を待つ間も段階として出す（送信で待っていることが分かる。#1147）。
      progress={<ChatProgress key={message.localId} steps={chatSubmitProgressSteps(message.sentAtMs)} testId="chat-progress" />}
      failure={
        <div data-testid="chat-send-failure">
          <Banner
            severity="danger"
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={RotateCcw}
                disabled={resendDisabled}
                onClick={onResend}
              >
                {t("chat.resend")}
              </Button>
            }
          >
            {errorMessage || t("chat.sendFailedHint")}
          </Banner>
        </div>
      }
    />
  );
}

function ToolStepRow({ step }: { step: RunStep }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span className="min-w-0 break-all font-mono text-fg">{step.tool_call?.name}</span>
      <StatusBadge {...stepStatusView(step.status)} />
    </li>
  );
}

function answerText(artifacts: Artifact[]): string | null {
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.kind === "answer" && typeof artifact.content.text === "string") {
      return artifact.content.text;
    }
  }
  return null;
}

/** RAG の結果（`rag_evidence` の成果物）のうち、回答の出典にする根拠（#1219）。 */
function runCitations(artifacts: Artifact[]): RagEvidenceView[] {
  return answerSources(artifacts.filter((artifact) => artifact.kind === "rag_evidence").map((artifact) => artifact.content));
}

/** 会話が消えた・読めなくなった失敗か（一時的な失敗ではなく、新しい会話に戻すもの）。 */
function isThreadGone(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}

function failureMessage(run: RunState): string {
  const event = [...run.events].reverse().find((item) => item.type === "runtime.failed");
  return event?.message || t("chat.failedUnknown");
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
