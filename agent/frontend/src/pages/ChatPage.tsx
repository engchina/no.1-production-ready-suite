import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import {
  Check,
  RefreshCw,
  RotateCcw,
  SearchX,
  Square,
  X,
} from "lucide-react";
import {
  AnswerNotice,
  Banner,
  ProcessFailureNotice,
  Button,
  Card,
  CardContent,
  ChatComposer,
  ChatHistoryList,
  chatHistoryWindowLabels,
  ChatLayout,
  ChatProgress,
  ChatSkeleton,
  useChatProgressStream,
  ChatAnswer,
  ChatPendingTurn,
  ChatTurn,
  EmptyState,
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
  useChatHistoryActions,
  useChatHistoryPanel,
  ApiErrorBanner,
  apiErrorMessage,
  DEFAULT_PAGE_SIZE,
  type ChatProgressEvent,
  type ClarificationBusyAction,
} from "@production-ready/ui";

import {
  ApiError,
  agentApi,
  runProgressStreamUrl,
  type ApprovalRequest,
  type Artifact,
  type ClarificationKind,
  type ConfirmationAction,
  type ConfirmationAnswer,
  type ConfirmationRequest,
  type RunState,
  type ThreadData,
  type ThreadSummary,
} from "@/lib/api";
import { isRunnableAgent } from "@/lib/agent-availability";
import {
  ANSWER_VALIDATION_KIND,
  answerReview,
  cancelledClarificationKinds,
  pendingClarificationKinds,
} from "@/lib/answer-review";
import { runAnswerEvidence } from "@/lib/answer-evidence";
import { noticeHeading, structureAnswer } from "@/lib/answer-structure";
import { AGENT_CHAT_PROGRESS_STEPS, chatSubmitProgressSteps } from "@/lib/chat-progress";
import { AnswerFeedback } from "@/components/chat/AnswerFeedback";
import { AnswerEvidenceBlock, StructuredAnswer } from "@/components/chat/AnswerEvidenceView";
import { AnswerReviewPanel } from "@/components/chat/AnswerReview";
import { ClarificationCancelActions, ClarificationCancelNote } from "@/components/chat/ClarificationCancel";
import { ConfirmationRecordBubble, PendingConfirmationCard } from "@/components/chat/RunConfirmation";
import { agentConfirmationRecords, pendingConfirmation, responseRecord } from "@/lib/confirmation";
import { agentPaginationLabels } from "@/components/ListViews";
import { AnswerBody, ToolResultTable } from "@/components/chat/ResultTables";
import { useAuth } from "@/components/security/AuthProvider";
import { t } from "@/lib/i18n";
import { processFailureLabels, runFailure } from "@/lib/process-failure";
import { useCapabilities } from "@/lib/permissions";
import { answerNoticeText } from "@/lib/run-artifacts";
import { withToolStepDetails } from "@/lib/run-history";
import { runToolResultTables } from "@/lib/run-tables";
import { runStatusView } from "@/lib/status-labels";
import { isNullableString, isString, useWorkspaceState } from "@/lib/workspace-state";
import { isOwnDecision } from "@/pages/shared/approval-decision";

/**
 * 業務利用者のチャット（#768）。使ってよい業務 Agent を選んで会話する。1 往復が 1 Run で、
 * 同じ会話の前の質問と回答は組み込み Runtime がモデルへ渡す。回答の上に処理の経過（使ったツールの段を開くと
 * 引数・結果）、回答の下に出典・承認待ちを出す。画面の型は RAG と同じ（会話は既定で全幅、履歴は必要なときに開く）。
 */

const ACTIVE_STATUSES = new Set<RunState["status"]>(["queued", "running"]);
/** 「停止」で止められる Run（回答の作成中と、ツールの承認待ち・回答前の確認の待ち。#805・#1839）。 */
const STOPPABLE_STATUSES = new Set<RunState["status"]>(["queued", "running", "waiting_approval", "waiting_confirmation"]);
/** 利用者の操作（承認・回答前の確認の答え）を待っている Run。次の質問は送らない。 */
const WAITING_STATUSES = new Set<RunState["status"]>(["waiting_approval", "waiting_confirmation"]);
const POLL_INTERVAL_MS = 1500;
/** 処理の段階の記録が無い Run（同じ参照を渡し、描画ごとに積み直さない）。 */
const NO_PROGRESS_EVENTS: readonly ChatProgressEvent[] = [];
// 持ち主以外が送った往復の無い会話（#1928）。
const NO_SENDER_NAMES: Record<string, string> = {};
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
  // 実行の詳細の「会話を開く」（`?agent_id=&thread_id=`。#2343）から開いた会話。URL の会話を一度だけ選び（作業状態に
  // 残す）、URL からは外す（読み込み直しや戻るで、選び直した会話を URL の会話に戻さない）。
  const [searchParams, setSearchParams] = useSearchParams();
  const linkedThreadId = searchParams.get("thread_id");
  const linkedAgentId = searchParams.get("agent_id");
  const [appliedLink, setAppliedLink] = useState<string | null>(null);
  if (linkedThreadId && linkedThreadId !== appliedLink) {
    setAppliedLink(linkedThreadId);
    if (linkedAgentId) setAgentId(linkedAgentId);
    setThreadId(linkedThreadId);
  } else if (!linkedThreadId && appliedLink) {
    // URL から外した後は、次に同じ会話を開いたときにも選び直す。
    setAppliedLink(null);
  }
  useEffect(() => {
    if (!linkedThreadId) return;
    const next = new URLSearchParams(searchParams);
    next.delete("thread_id");
    next.delete("agent_id");
    setSearchParams(next, { replace: true });
  }, [linkedThreadId, searchParams, setSearchParams]);
  // 送った質問（#907）。Run の作成の応答を待たずに会話の欄の末尾へ出し、作られた Run に置き換える。
  // 作れなかったときは残して「再送信」を出す（入力を失わない）。
  const [pending, setPending] = useState<OptimisticChatMessage | null>(null);
  // この画面で送った（答えで作った）Run。回答前の確認のカードが送信の続きで出たら、確認事項の文へフォーカスを移す
  // （UX 契約 confirm-before-answer.md §7。履歴から開いた会話のカードでは移さない。#1839）。
  const [sentRunIds, setSentRunIds] = useState<ReadonlySet<string>>(() => new Set());
  const rememberSentRun = (runId: string) => setSentRunIds((current) => new Set(current).add(runId));

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
  // 持ち主以外（system_admin）が送った往復の、送った人の表示名（#1928）。system_admin は他の利用者の会話でも、
  // 本人と同じく続きの質問・回答前の確認・評価・停止ができる（実行するのは操作した system_admin 自身）。
  const senderNames = thread.data?.sender_display_names ?? NO_SENDER_NAMES;
  const viewingOwnThread = thread.data?.owned !== false;
  // 処理の段階の終端が届いたら、回答・成果物を待たずに取り直す（#1359）。応答しない取得（取得中は interval が
  // 次を始めない）は打ち切って取り直す（#1160）。
  const refetchThread = thread.refetch;
  const refreshThread = () => refetchThread({ cancelRefetch: true, throwOnError: false });
  const running = runs.some((run) => ACTIVE_STATUSES.has(run.status));
  const waitingApproval = runs.some((run) => WAITING_STATUSES.has(run.status));
  const stoppableRun = [...runs].reverse().find((run) => STOPPABLE_STATUSES.has(run.status));
  const lastRunId = runs.at(-1)?.id;
  const lastRunStatus = runs.at(-1)?.status;

  // 会話の欄の自動スクロール（3 製品共通の useChatAutoScroll。#1161）。祖先（ページ・document）は動かさない。
  // 末尾を見ている間は新しい Run・状態の変化に合わせて末尾へ追い、上を読んでいる間は「最新へ」を出す。
  const autoScroll = useChatAutoScroll({
    contentKey: `${lastRunId ?? ""}:${lastRunStatus ?? ""}:${runs.at(-1)?.steps.length ?? 0}:${pending?.localId ?? ""}:${pending?.status ?? ""}`,
    resetKey: `${selectedAgentId}:${threadId ?? ""}`,
  });

  // 評価を保存した Run を、会話の取り直しを待たずに差し替える（#774）。操作の応答の Run には見ている利用者から見た
  // 事実（実行者・待ちの操作の可否。#1954）が無いので、今の値に重ねて残す。
  function replaceRun(updated: RunState) {
    queryClient.setQueryData<ThreadData>(["thread", threadId], (current) =>
      current
        ? {
            ...current,
            runs: current.runs.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)),
          }
        : current
    );
  }

  /**
   * 待ちの操作（承認・再開・Agent 自身の確認への回答）を見ている利用者ができるか（#1928）。backend の `can_decide` が
   * 正本で、無い応答（古い取得）は「自分の会話で、ほかの利用者（system_admin）が送った往復」を操作できないとみなす。
   */
  const runDecidable = (run: RunState) => run.can_decide ?? !(viewingOwnThread && run.id in senderNames);

  // 会話・エージェントを切り替えるたびに進める。送信の要求中に切り替えたら、その応答で
  // 元の会話へ引き戻したり、切り替え先の送信中・失敗の表示を書き換えたりしない（send.reset() では
  // useMutation の onSuccess / onError は止まらない）。
  const viewGenerationRef = useRef(0);
  // 送信の要求中に「停止」を押したら、Run ができた直後に止める（Run の id は応答で分かる。#805）。
  // 押したときの viewGeneration を持ち、その送信の応答でだけ止める。
  const stopAfterCreateRef = useRef<number | null>(null);
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
    mutationFn: ({ goal, clarificationCancel }: { goal: string; generation: number; clarificationCancel?: ClarificationKind }) =>
      agentApi.createRun({
        goal,
        agent_id: selectedAgentId,
        thread_id: threadId ?? undefined,
        ...(clarificationCancel ? { clarification_cancel: clarificationCancel } : {}),
      }),
    onSuccess: (run, { generation }) => {
      const stopRequested = stopAfterCreateRef.current === generation;
      if (stopRequested) stopAfterCreateRef.current = null;
      if (generation !== viewGenerationRef.current) {
        // 別の会話へ移った後の応答。Run は元の会話に残り、履歴から開ける。
        void queryClient.invalidateQueries({ queryKey: ["thread", run.thread_id] });
        void queryClient.invalidateQueries({ queryKey: ["threads"] });
        if (stopRequested) cancel.mutate(run.id);
        return;
      }
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
      rememberSentRun(run.id);
      void queryClient.invalidateQueries({ queryKey: ["thread", run.thread_id] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
      if (stopRequested) cancel.mutate(run.id);
    },
    onError: (_error, { generation }) => {
      if (stopAfterCreateRef.current === generation) stopAfterCreateRef.current = null;
      if (generation !== viewGenerationRef.current) return;
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

  /**
   * 回答前の確認（#1839）に答える・確認せずに回答する。Agent 自身の確認は同じ回答の続きが動き、ツールの結果の
   * 確認は答えで次の回答ができる（会話の末尾に足す）。確認を終えるのは「確認せずに回答」だけで、質問を取り消すのは
   * 入力欄の「停止」（#2311）。
   */
  const decideConfirmation = useMutation({
    mutationFn: ({
      run,
      confirmation,
      action,
      answers,
    }: {
      run: RunState;
      confirmation: ConfirmationRequest;
      action: ConfirmationAction;
      answers?: ConfirmationAnswer[];
      generation: number;
    }) => agentApi.decideConfirmation(run.id, confirmation.id, { action, ...(answers ? { answers } : {}) }),
    onSuccess: (outcome, { generation }) => {
      const outcomeThreadId = outcome.run.thread_id ?? threadId;
      if (generation !== viewGenerationRef.current) {
        void queryClient.invalidateQueries({ queryKey: ["thread", outcomeThreadId] });
        void queryClient.invalidateQueries({ queryKey: ["threads"] });
        return;
      }
      queryClient.setQueryData<ThreadData>(["thread", outcomeThreadId], (current) =>
        current
          ? {
              ...current,
              runs: [
                ...current.runs
                  .filter((item) => item.id !== outcome.next_run?.id)
                  .map((item) => (item.id === outcome.run.id ? outcome.run : item)),
                ...(outcome.next_run ? [outcome.next_run] : []),
              ],
            }
          : current
      );
      rememberSentRun(outcome.run.id);
      if (outcome.next_run) rememberSentRun(outcome.next_run.id);
      autoScroll.scrollToLatest();
      void queryClient.invalidateQueries({ queryKey: ["thread", outcomeThreadId] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });

  /**
   * 自分で質問し直す（#1954）。system_admin が自分の会話で実行して止まった Run は、持ち主には承認・回答できない（実行した
   * 人の権限で再開するため。#1928）。待たずに進めるときは、その Run を止めて、同じ質問を同じ会話で自分の権限の新しい
   * Run として送る（backend は操作した利用者を Run の利用者にする）。
   */
  const reask = useMutation({
    mutationFn: ({ run }: { run: RunState; generation: number }) =>
      STOPPABLE_STATUSES.has(run.status) ? agentApi.cancelRun(run.id) : Promise.resolve(run),
    onSuccess: (updated, { run, generation }) => {
      if (generation !== viewGenerationRef.current) {
        void queryClient.invalidateQueries({ queryKey: ["thread", run.thread_id] });
        return;
      }
      replaceRun(updated);
      setPending(createOptimisticChatMessage(run.goal));
      autoScroll.scrollToLatest();
      cancel.reset();
      send.mutate({ goal: run.goal, generation });
    },
  });

  function reaskAsSelf(run: RunState) {
    if (reask.isPending || send.isPending || prerequisitesLoading || threadFailed) return;
    reask.mutate({ run, generation: viewGenerationRef.current });
  }

  function onDecideConfirmation(
    run: RunState,
    confirmation: ConfirmationRequest,
    action: ConfirmationAction,
    answers?: ConfirmationAnswer[]
  ) {
    if (decideConfirmation.isPending || send.isPending) return;
    decideConfirmation.mutate({ run, confirmation, action, answers, generation: viewGenerationRef.current });
  }

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
    send.mutate({ goal, generation: viewGenerationRef.current });
  }

  /**
   * 確認の取り消し（#1441・#1445）。「データベースの検索は不要」「資料の検索は不要」を押すと、その文を利用者の発言として
   * 送り、その質問ではそのツールを使わずに続けさせる（入力欄の下書きは残す）。
   */
  function cancelClarification(kind: ClarificationKind) {
    const goal = t(`chat.clarificationCancel.${kind}`);
    if (!selectedAgentId || running || waitingApproval || send.isPending || prerequisitesLoading || threadFailed) return;
    setPending(createOptimisticChatMessage(goal));
    autoScroll.scrollToLatest();
    cancel.reset();
    send.mutate({ goal, generation: viewGenerationRef.current, clarificationCancel: kind });
  }

  /** 送れなかった質問を、そのままもう一度送る（#907）。 */
  function resend() {
    if (!pending || !selectedAgentId || running || waitingApproval || send.isPending) return;
    setPending(withOptimisticChatStatus(pending, "sending"));
    cancel.reset();
    send.mutate({ goal: pending.content, generation: viewGenerationRef.current });
  }

  function stop() {
    if (cancel.isPending) return;
    if (send.isPending) {
      stopAfterCreateRef.current = viewGenerationRef.current;
      return;
    }
    if (stoppableRun) cancel.mutate(stoppableRun.id);
  }

  function startNewThread() {
    viewGenerationRef.current += 1;
    setThreadId(null);
    setPending(null);
    send.reset();
    cancel.reset();
    decideConfirmation.reset();
    reask.reset();
    // 新しい会話はすぐ書き始められるよう、入力欄へフォーカスする（RAG と同じ）。
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  function openThread(item: ThreadSummary) {
    // lg 未満のシートは、会話を選んだら閉じる（SideSheet が開閉ボタンへフォーカスを戻す）。
    history.closeSheet();
    viewGenerationRef.current += 1;
    setThreadId(item.thread_id);
    setPending(null);
    send.reset();
    cancel.reset();
    decideConfirmation.reset();
    reask.reset();
  }

  // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す（RAG と同じ。#664）。
  const currentThreadId = threadOfOtherAgent ? null : threadId;
  const currentThreadTitle = currentThreadId
    ? (threads.data?.items.find((item) => item.thread_id === currentThreadId)?.title ?? runs[0]?.goal)
    : undefined;

  // 会話の名前の変更・削除（3 製品共通の useChatHistoryActions。#1817）。回答の作成中・承認待ちの会話は backend が
  // 削除しない（409）ので、送信は止めず、消せた後に開いている会話の選択を外す。
  const historyActions = useChatHistoryActions({
    onRename: async (id, title) => {
      await agentApi.renameThread(id, title);
      await queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
    onDelete: (id) => agentApi.deleteThread(id),
    onDeleted: (item) => {
      if (item.id === currentThreadId) startNewThread();
      queryClient.removeQueries({ queryKey: ["thread", item.id] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });

  // 会話の履歴に出す期間（最後の更新が直近 N 日。3 製品共通の文言。#2340）。
  const historyWindow = chatHistoryWindowLabels(threads.data?.history_days);
  // 一覧の行・読み込み中・失敗・0 件は 3 製品共通の ChatHistoryList（#1161）。
  const historyContent = (
    <ChatHistoryList
      items={(threads.data?.items ?? []).map((item) => ({
        id: item.thread_id,
        title: item.title,
        meta: user?.is_system_admin && item.owned === false
          ? // system_admin が見る他の利用者の会話には、利用者の表示名を添える（#1889）。
            t("chat.threads.owner", {
              meta: `${formatTime(item.updated_at)}・${t("chat.threads.turns", { count: item.run_count })}`,
              name: item.actor_display_name || t("chat.threads.ownerMissing"),
            })
          : `${formatTime(item.updated_at)}・${t("chat.threads.turns", { count: item.run_count })}`,
        badge:
          item.last_status === "completed" ? undefined : (
            <StatusBadge variant={runStatusView(item.last_status).variant} label={t(`chat.status.${item.last_status}`)} />
          ),
      }))}
      currentId={currentThreadId}
      onSelect={(item) => {
        const thread = threads.data?.items.find((candidate) => candidate.thread_id === item.id);
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
        // 期間で絞った一覧の 0 件は期間を含めた文（3 製品共通。#2340）。
        empty: historyWindow?.empty ?? t("chat.threads.empty"),
      }}
      testIds={{
        skeleton: "chat-threads-skeleton",
        loading: "chat-threads-loading",
        error: "chat-threads-error",
        list: "chat-thread-list",
        pagination: "chat-threads-pagination",
      }}
      renderActions={historyActions.renderActions}
      renderEditor={historyActions.renderEditor}
      // 会話が減って今のページが空になったら、共通の OffsetPagination が最後のページへ戻す（#1265 / #1266）。
      pagination={
        threads.data
          ? {
              type: "offset",
              offset: threads.data.offset,
              limit: DEFAULT_PAGE_SIZE,
              total: threads.data.total,
              count: threads.data.items.length,
              onPageChange: (_page, nextOffset) => setThreadsOffset(nextOffset),
              labels: agentPaginationLabels(),
              ariaLabel: t("chat.threads.pagination"),
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
        {/* カードの余白は CardContent の既定（見出しの無いカードは上も同じ pt-5）で、3 製品のチャットと同じ（#1778）。 */}
        <Card className="shrink-0">
          <CardContent className="pt-5">
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
                  viewGenerationRef.current += 1;
                  setAgentId(value);
                  setThreadId(null);
                  setPending(null);
                  send.reset();
                  decideConfirmation.reset();
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
            historyCaption={historyWindow?.caption}
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
                  // system_admin が他の利用者の会話を開いているときは、送る前に持ち主にも見えることを示す（#1954）。
                  // 新しい会話・自分の会話では出さない。
                  notice={
                    user?.is_system_admin && currentThreadId && thread.data?.owned === false
                      ? t("chat.composer.ownerSeesNotice")
                      : undefined
                  }
                  footer={
                    <>
                      {cancel.error ? (
                        // 停止の失敗は入力欄の下に出す（3 製品で同じ ApiErrorBanner。#1161）。
                        <ApiErrorBanner error={cancel.error} fallback={t("chat.stopFailed")} testId="chat-stop-error" />
                      ) : null}
                    </>
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
              runs.map((run, index) => (
                <RunChatTurn
                  key={run.id}
                  run={run}
                  // 取り消しの操作は、利用者の答えを待っている最後の回答だけに出す（送信中・実行中は押せない）。
                  latest={index === runs.length - 1 && !pending}
                  cancelDisabled={composerBlocked || send.isPending || threadLoading || threadFailed}
                  onCancelClarification={cancelClarification}
                  confirmationDisabled={
                    send.isPending ||
                    threadLoading ||
                    threadFailed ||
                    // Agent 自身の確認への答えは Run の利用者の権限で再開するので、自分の会話でも、ほかの利用者
                    // （system_admin）が送った往復には答えない（承認と同じ。backend も 404。#1928）。
                    (!runDecidable(run) && pendingConfirmation(run)?.source === "agent")
                  }
                  confirmationBusy={
                    decideConfirmation.isPending && decideConfirmation.variables?.run.id === run.id
                      ? clarificationBusyAction(decideConfirmation.variables.action)
                      : null
                  }
                  confirmationError={
                    decideConfirmation.variables?.run.id === run.id ? decideConfirmation.error : null
                  }
                  onDecideConfirmation={onDecideConfirmation}
                  // 入力欄に次の質問を書き始めていたら、その位置を奪わない。
                  confirmationAutoFocus={sentRunIds.has(run.id) && !draft.trim()}
                  // 持ち主以外（system_admin）が送った往復は、送った人の表示名を質問に添える（#1928）。
                  sender={
                    run.id in senderNames
                      ? t("chat.sender", { name: senderNames[run.id] || t("chat.threads.ownerMissing") })
                      : undefined
                  }
                  // 承認したツールは Run の利用者の権限で実行するため、自分の会話でも、ほかの利用者（system_admin）が
                  // 送った往復の承認は判断しない（backend も 404。#1928）。
                  canDecide={capabilities.decideApprovals && runDecidable(run)}
                  // 自分では操作できない待ちは、待っている相手（実行した system_admin）を出し、自分で質問し直せる（#1954）。
                  waitingFor={
                    !runDecidable(run) && WAITING_STATUSES.has(run.status)
                      ? run.executor_display_name || senderNames[run.id] || t("chat.threads.ownerMissing")
                      : undefined
                  }
                  onReask={capabilities.operateRuns ? () => reaskAsSelf(run) : undefined}
                  reaskBusy={reask.isPending && reask.variables?.run.id === run.id}
                  reaskDisabled={reask.isPending || send.isPending || threadLoading || threadFailed}
                  reaskError={reask.variables?.run.id === run.id ? reask.error : null}
                  // 評価は会話を読める利用者（本人と system_admin）が付け、評価した人を残す（#774・#1928）。
                  canRate={capabilities.operateRuns}
                  deciding={decide.isPending}
                  onDecide={(approval, approved) => decide.mutate({ approval, approved })}
                  onFeedbackSaved={replaceRun}
                  onProgressTerminal={refreshThread}
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
  latest,
  cancelDisabled,
  onCancelClarification,
  confirmationDisabled,
  confirmationBusy,
  confirmationError,
  onDecideConfirmation,
  confirmationAutoFocus,
  canDecide,
  canRate,
  deciding,
  onDecide,
  onFeedbackSaved,
  onProgressTerminal,
  sender,
  waitingFor,
  onReask,
  reaskBusy,
  reaskDisabled,
  reaskError,
}: {
  run: RunState;
  /** 持ち主以外（system_admin）が送った往復の、送った人の表示（翻訳済み。#1928）。 */
  sender?: string;
  /**
   * 自分では操作できない待ち（system_admin が実行した Run の承認・Agent 自身の確認。#1928）の、待っている相手の表示名。
   * 指定すると承認・確認の操作を出さず、待っている相手と「自分で質問し直す」を出す（#1954）。
   */
  waitingFor?: string;
  /** 自分で質問し直す（同じ質問を自分の権限の新しい Run で送る。実行の権限が無ければ無し）。 */
  onReask?: () => void;
  reaskBusy: boolean;
  reaskDisabled: boolean;
  reaskError: unknown;
  /** 会話の最後の往復か（確認の取り消しの操作を出すのは最後の回答だけ）。 */
  latest: boolean;
  cancelDisabled: boolean;
  onCancelClarification: (kind: ClarificationKind) => void;
  /** 回答前の確認のカードを操作できない（送信中・会話の読み込み中）。 */
  confirmationDisabled: boolean;
  /** この往復の確認で処理中の操作。 */
  confirmationBusy: ClarificationBusyAction | null;
  confirmationError: unknown;
  /** 確認のカードが出たときに確認事項の文へフォーカスを移すか（この画面で送った往復）。 */
  confirmationAutoFocus: boolean;
  onDecideConfirmation: (
    run: RunState,
    confirmation: ConfirmationRequest,
    action: ConfirmationAction,
    answers?: ConfirmationAnswer[]
  ) => void;
  canDecide: boolean;
  canRate: boolean;
  deciding: boolean;
  onDecide: (approval: ApprovalRequest, approved: boolean) => void;
  onFeedbackSaved: (run: RunState) => void;
  /** 処理の段階の終端が届いた（回答・成果物を待たずに取り直す）。 */
  onProgressTerminal: () => void;
}) {
  const answer = answerText(run.artifacts);
  // 回答の根拠（AI に渡した根拠・回答で引用した根拠。RAG のチャットと同じ塊。#2328）。
  const evidence = useMemo(() => runAnswerEvidence(run.artifacts), [run.artifacts]);
  const toolTables = runToolResultTables(run);
  const pendingApprovals = run.approvals.filter((approval) => approval.status === "pending");
  // 資料との照合（照合の結果のバッジと、確かめた条件・確認待ちの質問・業務ガイド。#1286・#2361）。作成中は出さない。
  const review = ACTIVE_STATUSES.has(run.status) ? null : answerReview(run.artifacts);
  // 回答の本文の節（要約 →「資料により確認できた事項」と根拠の行 →「資料から確認できなかった事項」。RAG のチャットと
  // 同じ形。#2328）。段落に根拠を結び付けられないときは今までの Markdown の描き方にする。
  const answerBlocks = useMemo(
    () =>
      answer && evidence
        ? structureAnswer(answer, evidence, noticeHeading(latestContent(run.artifacts, ANSWER_VALIDATION_KIND)), {
            confirmed: t("chat.answer.confirmed"),
            unconfirmed: t("chat.answer.unconfirmed"),
          })
        : null,
    [answer, evidence, run.artifacts]
  );
  // 確認の取り消し（#1441・#1445）。取り消した往復には補足の文を、利用者の答えを待っている最後の回答には操作を出す。
  const cancelledKinds = cancelledClarificationKinds(run);
  // 回答前の確認（#1839）。答えていない確認は最後の往復だけにカードで出し、答えた内容は利用者の側に残す。
  // 自分では答えられない Agent 自身の確認（system_admin が実行した Run。#1928）はカードを出さない（#1954）。
  const latestConfirmation = latest ? pendingConfirmation(run) : null;
  const confirmation =
    waitingFor !== undefined && latestConfirmation?.source === "agent" ? null : latestConfirmation;
  const toolConfirmationKind: ClarificationKind | null =
    confirmation && confirmation.source !== "agent" ? confirmation.source : null;
  const response = responseRecord(run);
  const agentRecords = agentConfirmationRecords(run);
  // 確認の取り消し（「データベースの検索は不要」など）は、カードの確認事項の種類ならカードの操作に置く。
  const cancelKinds = (
    latest && run.status === "completed" ? pendingClarificationKinds(run.artifacts) : []
  ).filter((kind) => kind !== toolConfirmationKind);
  // 処理の段階（3 製品共通のイベント。#1359）。backend が Run の状態から記録したイベントを、実行中は SSE で受け取り
  // （承認待ちの間も heartbeat で途絶えない）、使えなければ polling に縮退する。会話の取り直しに入っている記録も積む。
  // 途絶えたら共通の hook が取り直し、「接続を確認しています」を出す（#1160）。
  const stoppable = STOPPABLE_STATUSES.has(run.status);
  const runId = run.id;
  const fetchProgress = useCallback(
    (since: number, signal: AbortSignal) => agentApi.getRunProgress(runId, since, { signal }),
    [runId]
  );
  const progress = useChatProgressStream({
    key: run.id,
    definitions: AGENT_CHAT_PROGRESS_STEPS,
    events: run.progress_events ?? NO_PROGRESS_EVENTS,
    streamUrl: stoppable ? runProgressStreamUrl(run.id) : null,
    fetchEvents: fetchProgress,
    active: stoppable,
    // 途絶えの判定は共通の既定（15 秒。SSE の heartbeat の 10 秒より長い）。
    // 段階の配信で先に終わりが届いたら、回答・成果物を会話の取り直しを待たずに取りに行く（会話の取得で
    // 終わった Run を開いたときは取り直さない）。
    onTerminal: () => {
      if (stoppable) onProgressTerminal();
    },
  });
  const failure = runFailure(run, progress.steps, "chat");
  // ツールの段に、Run の step の引数・結果・エラーを段を開いて見る詳細として結ぶ（実行の詳細と同じ対応。#2294）。
  // 結果は会話の取り直し（実行中は 1.5 秒ごと・終わったとき）で届く。
  const progressSteps = progress.progressProps.steps;
  const runSteps = run.steps;
  const stepsWithDetails = useMemo(() => withToolStepDetails(progressSteps, runSteps), [progressSteps, runSteps]);

  return (
    // 1 往復の入れ物（質問の吹き出し・回答の枠）は 3 製品共通の ChatTurn / ChatAnswer（#1161）。
    <ChatTurn
      question={run.goal}
      questionSender={sender}
      // カードで答えて作った往復は、質問の吹き出しの代わりに「回答前に確認した内容」を出す（#1839）。
      userContent={
        response ? <ConfirmationRecordBubble record={response} testId={`chat-confirmation-record-${run.id}`} /> : undefined
      }
      testId={`chat-turn-${run.id}`}
    >
      {agentRecords.map((record) => (
        // Agent 自身の確認に答えた内容は、同じ往復の利用者の側に残す（回答の枠の上）。
        <ConfirmationRecordBubble key={record.key} record={record} testId={`chat-confirmation-record-${record.key}`} />
      ))}
      <ChatAnswer live>
        {/*
          処理の段階（考えている・ツールの呼び出し・承認待ち・回答の作成。#1147）。共通のイベントの配信で更新し（#1359）、
          完了後は回答の上に「処理の経過（N ステップ・M 秒）」を閉じて出す（実行中は開く。#2096 / #2121 / #2265。共有の ChatProgress。
          3 製品で同じ。#1145）。
        */}
        {/* 経過時間は Run の作成（送信の受付）から数え続ける（段階ごとに 0 に戻さない。#1176）。 */}
        {/* 承認・回答前の確認の答えは人の操作を待つので、遅延の案内を出さない（実行の詳細と同じ。#1839）。 */}
        <ChatProgress
          {...progress.progressProps}
          steps={stepsWithDetails}
          startedAt={run.created_at}
          slowAfterMs={WAITING_STATUSES.has(run.status) ? Number.POSITIVE_INFINITY : undefined}
          testId="chat-progress"
        />
        {failure ? (
          // 失敗は 3 製品共通の失敗のカード（止まった段・原因・次の操作・「詳細」。#2342）。進めない理由を先に
          // 読めるよう、処理の経過の直下（回答・結果の上）に 1 つだけ出す。
          <ProcessFailureNotice failure={failure} labels={processFailureLabels()} testId={`chat-failure-${run.id}`} />
        ) : null}
        {/*
          使ったツールは処理の経過のツールの段（表示名・状態、開くと内部名・引数・結果・データの範囲。#2294）で見る。
          同じ内容を「使ったツール」の一覧で 2 か所に出さない（#2324）。
        */}
        {cancelledKinds.length > 0 ? (
          <ClarificationCancelNote kinds={cancelledKinds} testId={`chat-clarification-cancelled-${run.id}`} />
        ) : null}
        {answer ? (
          // 回答の Markdown の表と、表の形のツールの結果（NL2SQL の SQL の実行の結果など）は、NL2SQL のチャットと
          // 同じ共通の結果の表で出す（#1158）。表でない部分は見出し・太字・箇条書きなどを描く（#1808）。
          // チャットの画面にはページの見出し（h1）の下の見出しが無いため、回答の見出しは h2 から始める。
          answerBlocks && evidence ? (
            <StructuredAnswer runId={run.id} blocks={answerBlocks} evidence={evidence} testId={`chat-answer-${run.id}`} />
          ) : (
            <AnswerBody text={answer} headingLevel={2} testId={`chat-answer-${run.id}`} />
          )
        ) : null}
        {toolTables.map((table) => (
          <ToolResultTable
            key={table.stepId}
            data={table.data}
            toolName={table.toolName}
            sql={table.sql}
            testId={`chat-tool-table-${table.stepId}`}
          />
        ))}
        {confirmation ? (
          <PendingConfirmationCard
            key={confirmation.id}
            confirmation={confirmation}
            disabled={confirmationDisabled}
            busyAction={confirmationBusy}
            error={confirmationError}
            autoFocus={confirmationAutoFocus}
            actions={
              toolConfirmationKind ? (
                <Button
                  type="button"
                  variant="secondary"
                  icon={SearchX}
                  // カードの操作の行と同じく、375px では全幅で縦に並べる（共通のカードのボタンと同じ）。
                  className="w-full sm:w-auto"
                  disabled={cancelDisabled || confirmationBusy !== null}
                  onClick={() => onCancelClarification(toolConfirmationKind)}
                  data-testid={`chat-clarification-cancel-${run.id}-${toolConfirmationKind}`}
                >
                  {t(`chat.clarificationCancel.${toolConfirmationKind}`)}
                </Button>
              ) : undefined
            }
            onDecide={(action, answers) => onDecideConfirmation(run, confirmation, action, answers)}
          />
        ) : null}
        {run.status === "cancelled" ? (
          // 止めた回答の状態（途中までの回答は上に残す）。色だけに頼らず、停止のアイコンを添える（#805）。
          <p className="flex items-start gap-1.5 text-sm text-fg-muted" data-testid="chat-cancelled">
            {/* 折り返しても文字の 1 行目の中心にそろえる（#2371）。 */}
            <span className="pr-icon-line">
              <Square size={14} aria-hidden="true" />
            </span>
            <span className="min-w-0">{t("chat.cancelled")}</span>
          </p>
        ) : null}

        {waitingFor !== undefined ? (
          // 自分では操作できない待ち（#1954）。操作のボタンは出さず、待っている相手を出す（messaging.md §10。info の Banner）。
          <div className="space-y-2" data-testid={`chat-waiting-for-executor-${run.id}`}>
            <Banner
              severity="info"
              title={t("run.waitingForExecutor", { name: waitingFor })}
              action={
                onReask ? (
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={RotateCcw}
                    loading={reaskBusy}
                    disabled={reaskDisabled && !reaskBusy}
                    onClick={onReask}
                    data-testid={`chat-reask-${run.id}`}
                  >
                    {t("chat.reaskAsSelf")}
                  </Button>
                ) : undefined
              }
            >
              {t("chat.waitingForExecutorHint")}
            </Banner>
            {reaskError ? (
              <ApiErrorBanner error={reaskError} fallback={t("chat.reaskFailed")} testId={`chat-reask-error-${run.id}`} />
            ) : null}
          </div>
        ) : null}

        {(waitingFor !== undefined ? [] : pendingApprovals).map((approval) => (
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

        {cancelKinds.length > 0 ? (
          <ClarificationCancelActions
            kinds={cancelKinds}
            disabled={cancelDisabled}
            onCancel={onCancelClarification}
            testId={`chat-clarification-cancel-${run.id}`}
          />
        ) : null}

        {/* 資料との照合は回答の本文の下・出典の上（#1286。#2123 で元の位置に戻した。#2361 で「回答の確かめ」から改名）。 */}
        {review ? (
          <AnswerReviewPanel
            // 確認のカードを出している間は、同じ確認事項を「確認待ちの質問」に重ねて出さない（#1839）。
            review={toolConfirmationKind ? { ...review, clarifications: [] } : review}
            testId={`chat-review-${run.id}`}
          />
        ) : null}

        {/* 回答の根拠は回答の本文の下・回答の評価の前（RAG のチャットと同じ位置と部品。#2328）。 */}
        {evidence ? (
          <AnswerEvidenceBlock
            runId={run.id}
            evidence={evidence}
            idPrefix={`chat-${run.id}`}
            className="border-t border-border px-2 pt-1"
          />
        ) : null}

        {/* 回答の注意書きは回答の本文と根拠の後（3 製品共通の AnswerNotice。#2405）。 */}
        {run.status === "completed" ? (
          <AnswerNotice notice={answerNoticeText(run.artifacts)} label={t("answer.notice.label")} />
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

/** 確認の操作 → カードで処理中にするボタン。 */
function clarificationBusyAction(action: ConfirmationAction): ClarificationBusyAction {
  return action === "answer" ? "confirm" : action;
}

function answerText(artifacts: Artifact[]): string | null {
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.kind === "answer" && typeof artifact.content.text === "string") {
      return artifact.content.text;
    }
  }
  return null;
}

/** 最も新しい成果物の内容（無ければ null）。 */
function latestContent(artifacts: Artifact[], kind: string): Record<string, unknown> | null {
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.kind === kind) return artifact.content;
  }
  return null;
}

/** 会話が消えた・読めなくなった失敗か（一時的な失敗ではなく、新しい会話に戻すもの）。 */
function isThreadGone(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 403);
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
