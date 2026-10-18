import { useCallback, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Play, RefreshCw, Sparkles } from "lucide-react";
import {
  ApiErrorBanner,
  Banner,
  Button,
  ChatAnswer,
  ChatProgress,
  ChatSkeleton,
  ChatTurn,
  DEFAULT_PAGE_SIZE,
  EmptyState,
  ExecutionConfirmationField,
  MessageText,
  ProcessingIndicator,
  StatusBadge,
  TimedLoadingState,
  createOptimisticChatMessage,
  useChatAutoScroll,
  useChatHistoryActions,
  useChatHistoryPanel,
  withOptimisticChatStatus,
  type OptimisticChatMessage,
} from "@production-ready/ui";

import {
  useWorkspaceActive,
  useWorkspaceIdentity,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { useAuth } from "@/features/security/AuthProvider";
import { ApiError, apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/lib/api";
import { t } from "@/lib/i18n";
import { randomUuid } from "@/lib/randomUuid";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";

import { CHAT_PROGRESS_LABELS } from "./chatProgress";
import {
  ChatSqlBlock,
  Nl2SqlChatComposer,
  Nl2SqlChatHistoryList,
  Nl2SqlChatLayout,
  Nl2SqlChatPage,
  Nl2SqlPendingTurn,
  normalizeHistoryOffset,
} from "./components/ChatScaffold";
import { ChatLastExecutionText, ChatQueryResult } from "./components/ChatSqlExecution";
import { DbAdminExecutionResult } from "./components/DbAdminShared";
import { SchemaSyncNotice } from "./components/SchemaRefreshFeedback";
import { DEFAULT_SQL_ROW_LIMIT } from "./components/SqlRowLimitControls";
import {
  DB_ADMIN_CHAT_TURN_LIMIT,
  dbAdminChatCanExecute,
  dbAdminChatConfirmed,
  dbAdminChatGenerateStep,
  dbAdminChatProgressSteps,
  dbAdminChatSavedExecution,
  dbAdminExecutionSucceeded,
  dbAdminFailureText,
  type DbAdminChatExecution,
} from "./dbAdminChat";
import { nl2sqlIncrementalKeys } from "./incrementalQueries";
import type {
  DbAdminChatAnalyzeData,
  DbAdminChatConversation,
  DbAdminChatData,
  DbAdminChatPlan,
  DbAdminChatSendData,
  DbAdminChatTurn as DbAdminChatTurnData,
  DbAdminExecuteData,
  OffsetPage,
} from "./types";
import { useMutationSchemaSync } from "./useMutationSchemaSync";

/** 送った質問（生成の応答の前・送れなかったとき）。ターンの ID は送信の前に決め、送り直しも同じ ID。 */
interface PendingGenerate {
  message: OptimisticChatMessage;
  turnId: string;
  error: unknown;
}

const TEST_ID = "db-admin-chat";
const conversationPath = (id: string) =>
  `/api/nl2sql/db-admin-chat/conversations/${encodeURIComponent(id)}`;

/**
 * DB 管理チャット（#2264）。質問から Oracle の SQL（SELECT・DML・DDL・DCL・PL/SQL）を Enterprise AI Direct で
 * 作り、生成した SQL を必ず見せる。SELECT は「実行」で、それ以外は実行確認語を入力して実行する（自動では
 * 実行しない）。会話とターンはサーバーが保存し、「チャット」と同じ会話の履歴のパネル（開き直し・名前の
 * 変更・削除・新しい会話）を持つ（#2319）。画面の骨格・会話の履歴・入力欄・送信中の往復・SQL の表示・
 * 結果の表・処理の経過は「チャット」と同じ部品（ChatScaffold・ChatQueryResult・共通の ChatProgress）で描く。
 */
export function DbAdminChatPage() {
  const active = useWorkspaceActive();
  const { user } = useAuth();
  // 「AI 活用」と同じ見え方（#1889）: system_admin は全員の会話を見られ、履歴に利用者を出す。
  const seesAllUsers = Boolean(user?.is_system_admin);
  const identity = useWorkspaceIdentity();
  const queryClient = useQueryClient();
  const [conversationId, setConversationId] = useWorkspaceState("conversationId", "");
  const [draft, setDraft] = useWorkspaceState(`draft:${conversationId}`, "");
  const [historyPanelOpen, setHistoryPanelOpen] = useWorkspaceState("historyOpen", false);
  // 開閉の判定・シートの開閉は共通の useChatHistoryPanel（3 製品共通。#1161）。
  const historyPanel = useChatHistoryPanel({
    inlineOpen: historyPanelOpen,
    onInlineOpenChange: setHistoryPanelOpen,
    id: "db-admin-chat-history",
  });
  const [historyOffset, setHistoryOffset] = useWorkspaceState<number>("historyOffset", 0);
  const historyPageOffset = normalizeHistoryOffset(historyOffset);
  const [pending, setPending] = useState<PendingGenerate | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const generating = pending?.message.status === "sending";
  const chatKey = useMemo(
    () => ["nl2sql", "db-admin-chats", identity.owner, identity.context] as const,
    [identity.owner, identity.context],
  );
  const history = useQuery({
    // 会話の key（[...chatKey, 会話の ID]）と重ならないよう、一覧は object の要素で分ける。
    queryKey: [...chatKey, { list: historyPageOffset }],
    enabled: active,
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({
        limit: String(DEFAULT_PAGE_SIZE),
        offset: String(historyPageOffset),
      });
      return apiGet<OffsetPage<DbAdminChatConversation>>(
        `/api/nl2sql/db-admin-chat/conversations?${params.toString()}`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveList },
      );
    },
    placeholderData: keepPreviousData,
  });
  const conversation = useQuery({
    queryKey: [...chatKey, conversationId],
    enabled: active && Boolean(conversationId),
    queryFn: ({ signal }) =>
      apiGet<DbAdminChatData>(conversationPath(conversationId), {
        signal,
        timeoutMs: API_TIMEOUT_MS.interactiveDetail,
      }),
  });
  const turns = useMemo(() => conversation.data?.turns ?? [], [conversation.data]);
  // 開いている会話が他の利用者の物か（system_admin だけが開ける。#1928・#1954）。
  const ownerSeesNotice =
    seesAllUsers && Boolean(conversationId) && conversation.data?.conversation.owned === false;
  const conversationLoading = Boolean(conversationId) && conversation.isPending && !pending;
  const atLimit = turns.length >= DB_ADMIN_CHAT_TURN_LIMIT;
  const blocked =
    generating ||
    atLimit ||
    (Boolean(conversationId) && (conversation.isPending || conversation.isError));

  const autoScroll = useChatAutoScroll({
    contentKey: [
      turns.length,
      turns.map((turn) => `${turn.revision}:${turn.last_execution?.executed_at ?? ""}`).join(","),
      pending?.message.localId ?? "",
      pending?.message.status ?? "",
    ].join(":"),
    resetKey: conversationId,
    enabled: active,
  });

  const generate = useCallback(
    async (question: string, message: OptimisticChatMessage, turnId: string) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setPending({ message, turnId, error: null });
      try {
        // 前の往復は送らない（サーバーが保存した会話から復元する。#2319）。
        const data = await apiPost<DbAdminChatSendData>(
          "/api/nl2sql/db-admin-chat/generate",
          { question, conversation_id: conversationId, client_turn_id: turnId },
          { signal: controller.signal, timeoutMs: API_TIMEOUT_MS.longRunningJob },
        );
        const id = data.conversation.id;
        queryClient.setQueryData<DbAdminChatData>([...chatKey, id], (previous) => ({
          conversation: data.conversation,
          turns: [
            ...(previous?.turns ?? []).filter((turn) => turn.turn_id !== data.turn.turn_id),
            data.turn,
          ],
        }));
        setConversationId(id);
        setPending(null);
        void queryClient.invalidateQueries({ queryKey: chatKey, exact: false });
      } catch (error) {
        if (controller.signal.aborted) {
          // 停止した質問は入力欄に戻す（停止した生成はサーバーも保存しない。生成は副作用が無いので送り直せる）。
          setPending(null);
          setDraft(question);
          return;
        }
        setPending({ message: withOptimisticChatStatus(message, "failed"), turnId, error });
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [chatKey, conversationId, queryClient, setConversationId, setDraft],
  );

  const submit = () => {
    const question = draft.trim();
    if (!question || blocked) return;
    setDraft("");
    autoScroll.scrollToLatest();
    void generate(question, createOptimisticChatMessage(question), randomUuid());
  };

  const resend = () => {
    if (!pending || generating) return;
    // 同じターンの ID で送るので、応答が届かなかっただけならサーバーは保存したターンを返す（二重に生成しない）。
    void generate(
      pending.message.content,
      withOptimisticChatStatus(pending.message, "sending"),
      pending.turnId,
    );
  };

  const resetConversation = () => {
    if (generating) return;
    setConversationId("");
    setPending(null);
    composerRef.current?.focus();
  };

  const openConversation = (item: DbAdminChatConversation) => {
    if (generating) return;
    setConversationId(item.id);
    setPending(null);
    historyPanel.closeSheet();
  };

  // 会話の名前の変更・削除（3 製品共通の useChatHistoryActions。#1817）。消せた後に開いている会話なら
  // 新しい会話に戻す。
  const historyActions = useChatHistoryActions({
    onRename: async (id, title) => {
      await apiPatch(conversationPath(id), { title });
      await queryClient.invalidateQueries({ queryKey: chatKey });
    },
    onDelete: (id) => apiDelete(conversationPath(id)),
    onDeleted: (item) => {
      if (item.id === conversationId) resetConversation();
      queryClient.removeQueries({ queryKey: [...chatKey, item.id] });
      void queryClient.invalidateQueries({ queryKey: chatKey });
    },
    disabled: generating,
    idPrefix: "db-admin-chat-history-title",
  });

  const replaceTurn = useCallback(
    (turn: DbAdminChatTurnData) => {
      queryClient.setQueryData<DbAdminChatData>([...chatKey, conversationId], (previous) =>
        previous
          ? {
              ...previous,
              turns: previous.turns.map((item) => (item.turn_id === turn.turn_id ? turn : item)),
            }
          : previous,
      );
    },
    [chatKey, conversationId, queryClient],
  );
  const refreshConversation = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: [...chatKey, conversationId] });
  }, [chatKey, conversationId, queryClient]);

  return (
    // 見出しと本文の高さの渡し方はチャットと共通の Nl2SqlChatPage（#2332）。
    <Nl2SqlChatPage
      title={t("nav.dbAdminChat")}
      subtitle={t("dbAdminChat.subtitle")}
      testId="nl2sql-db-admin-chat"
    >
      <Nl2SqlChatLayout
        history={{
          panel: historyPanel,
          // 一覧の行・読み込み中・失敗・0 件・ページングはチャットと共通の Nl2SqlChatHistoryList（#2319）。
          content: (
            <Nl2SqlChatHistoryList
              page={history.data}
              loading={history.isPending}
              error={history.isError ? history.error : null}
              onRetry={() => void history.refetch()}
              retrying={history.isFetching}
              currentId={conversationId || null}
              onSelect={openConversation}
              disabled={generating}
              seesAllUsers={seesAllUsers}
              offset={historyPageOffset}
              onOffsetChange={setHistoryOffset}
              actions={historyActions}
              testIdPrefix={TEST_ID}
            />
          ),
        }}
        label={t("dbAdminChat.conversation")}
        // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す。
        conversationTitle={conversationId ? conversation.data?.conversation.title : null}
        conversationTitleLoading={Boolean(conversationId) && conversation.isPending}
        onNewConversation={resetConversation}
        newConversationDisabled={generating || (!conversationId && !pending)}
        autoScroll={autoScroll}
        testIdPrefix={TEST_ID}
        composer={
          <Nl2SqlChatComposer
            id={`${TEST_ID}-composer`}
            value={draft}
            onValueChange={setDraft}
            onSubmit={submit}
            onStop={() => abortRef.current?.abort()}
            running={generating}
            textareaRef={composerRef}
            submitBlocked={blocked}
            sendTestId={`${TEST_ID}-send`}
            // system_admin が他の利用者の会話で入力するとき、持ち主にも見えることを送る前に示す（#1954）。
            notice={ownerSeesNotice ? t("chat.composer.ownerSeesNotice") : undefined}
            footer={atLimit ? <Banner severity="info">{t("chat.limit")}</Banner> : null}
          />
        }
      >
        {conversationLoading ? (
          // 会話の内容の読み込み中は文言と経過時間を出し、会話の形の Skeleton で寸法を予約する（チャットと同じ）。
          <TimedLoadingState
            label={t("chat.loading")}
            operationKey="db-admin-chat-conversation-load"
            framed={false}
            testId={`${TEST_ID}-conversation-loading`}
          >
            <ChatSkeleton />
          </TimedLoadingState>
        ) : null}
        {conversationId && conversation.isError ? (
          <ApiErrorBanner
            error={conversation.error}
            fallback={t("chat.loadFailed")}
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={RefreshCw}
                onClick={() => void conversation.refetch()}
              >
                {t("chat.retry")}
              </Button>
            }
          />
        ) : null}
        {!conversationId && !pending ? (
          <EmptyState title={t("chat.empty")} hint={t("dbAdminChat.emptyHint")} />
        ) : null}
        {turns.map((turn) => (
          <DbAdminChatTurn
            key={`${turn.turn_id}:${turn.revision}`}
            conversationId={conversationId}
            turn={turn}
            onReplaceTurn={replaceTurn}
            onExecuted={refreshConversation}
          />
        ))}
        {pending ? (
          // 送った質問は生成の応答を待たずに出し、失敗しても残して「再送信」を出す（チャットと同じ。#907）。
          <Nl2SqlPendingTurn
            message={pending.message}
            error={pending.message.status === "failed" ? pending.error : null}
            fallback={t("dbAdminChat.sendFailed")}
            onResend={resend}
            resendDisabled={generating}
            steps={[dbAdminChatGenerateStep("running", pending.message.sentAtMs)]}
            testIdPrefix={TEST_ID}
          />
        ) : null}
      </Nl2SqlChatLayout>
    </Nl2SqlChatPage>
  );
}

function PlanBadges({ plan, executed }: { plan: DbAdminChatPlan; executed: boolean }) {
  const types = [...new Set(plan.statements.map((item) => item.statement_type))];
  return (
    <>
      {plan.blocked ? (
        <StatusBadge variant="danger" label={t("dbAdminChat.badge.blocked")} />
      ) : plan.read_only ? (
        <StatusBadge
          variant="success"
          label={executed ? t("dbAdminChat.badge.readOnlyExecuted") : t("dbAdminChat.badge.readOnly")}
        />
      ) : (
        <StatusBadge
          variant="warning"
          label={
            executed ? t("dbAdminChat.badge.confirmationExecuted") : t("dbAdminChat.badge.confirmation")
          }
        />
      )}
      {types.map((type) => (
        <StatusBadge key={type} icon={false} variant="neutral" label={type} />
      ))}
    </>
  );
}

type AnalysisState =
  | { status: "running" }
  | { status: "done"; data: DbAdminChatAnalyzeData }
  | { status: "failed"; error: unknown };

/**
 * 1 往復の実行の状態。この画面で受け取った結果（行を持つ）は TanStack Query のキャッシュ（メモリ）に置き、
 * 会話を切り替えて戻っても同じ画面の中では出し直す（チャットと同じ。sessionStorage などには保存しない）。
 * 受け取っていないとき（開き直した会話）はサーバーが保存した最後の実行の要約（行は無い）。
 */
function useDbAdminChatExecution(turn: DbAdminChatTurnData) {
  const identity = useWorkspaceIdentity();
  const queryClient = useQueryClient();
  const key = useMemo(
    () =>
      [
        "nl2sql",
        "db-admin-chat-execution",
        identity.owner,
        identity.context,
        turn.turn_id,
        turn.revision,
      ] as const,
    [identity.owner, identity.context, turn.turn_id, turn.revision],
  );
  const cached = useQuery<DbAdminChatExecution | null>({
    queryKey: key,
    queryFn: () => null,
    enabled: false,
    staleTime: Infinity,
    gcTime: 30 * 60_000,
  });
  const set = useCallback(
    (value: DbAdminChatExecution) => queryClient.setQueryData(key, value),
    [key, queryClient],
  );
  return { execution: cached.data ?? dbAdminChatSavedExecution(turn), set };
}

/** 1 往復の回答（生成した SQL・確認語・実行の結果・失敗の分析）。 */
function DbAdminChatTurn({
  conversationId,
  turn,
  onReplaceTurn,
  onExecuted,
}: {
  conversationId: string;
  turn: DbAdminChatTurnData;
  onReplaceTurn: (turn: DbAdminChatTurnData) => void;
  onExecuted: () => void;
}) {
  const queryClient = useQueryClient();
  const { plan } = turn;
  const sentAtMs = Date.parse(turn.created_at);
  const generatedAtMs = Date.parse(turn.generated_at);
  const [confirmation, setConfirmation] = useState("");
  const { execution, set: setExecution } = useDbAdminChatExecution(turn);
  const [analysis, setAnalysis] = useState<AnalysisState | null>(null);
  const [copyError, setCopyError] = useState("");
  const refreshSchemaReadModels = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
    void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
    void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
  }, [queryClient]);
  // DDL の後の DB 構造の同期の案内と進行は、実行の結果の直下に出す（管理 SQL を実行と同じ。#2031）。
  const schemaSync = useMutationSchemaSync({
    noticeOrigin: `db-admin-chat-notice-${turn.turn_id}`,
    onDone: refreshSchemaReadModels,
  });
  const running = execution?.status === "running";
  const result = execution?.result ?? null;
  const executeError = execution?.requestError ?? null;
  const executed = Boolean(result && dbAdminExecutionSucceeded(result));
  // 開き直した SELECT の成功は行を持たない（要約だけ）。
  const savedSelect =
    !result?.select_result && executed && typeof execution?.rowCount === "number"
      ? turn.last_execution
      : null;
  const failed = Boolean(
    execution && execution.status === "failed" && (executeError || (result && !executed)),
  );
  const canExecute = dbAdminChatCanExecute(plan, confirmation) && !running;

  const execute = async () => {
    if (!canExecute) return;
    const startedAtMs = Date.now();
    setExecution({ status: "running", startedAtMs });
    setAnalysis(null);
    schemaSync.reset();
    try {
      // SQL は送らない（サーバーがターンに保存した SQL を実行する。#2319）。
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin-chat/execute", {
        conversation_id: conversationId,
        turn_id: turn.turn_id,
        confirmation: plan.requires_confirmation ? confirmation.trim() : "",
        row_limit: DEFAULT_SQL_ROW_LIMIT,
      });
      const ok = dbAdminExecutionSucceeded(data);
      setExecution({ status: ok ? "done" : "failed", startedAtMs, finishedAtMs: Date.now(), result: data });
      // 実行の確認は 1 回ごと（もう一度実行するときは入力し直す）。
      setConfirmation("");
      onExecuted();
      const tracked = schemaSync.track(data);
      if (data.committed && !tracked) {
        void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
      }
    } catch (error) {
      setExecution({
        status: "failed",
        startedAtMs,
        finishedAtMs: Date.now(),
        result: null,
        requestError: error,
      });
    }
  };

  const analyze = async () => {
    setAnalysis({ status: "running" });
    const resultText = result
      ? dbAdminFailureText(result)
      : executeError instanceof ApiError
        ? executeError.message
        : String(executeError ?? "");
    try {
      const data = await apiPost<DbAdminChatAnalyzeData>(
        "/api/nl2sql/db-admin-chat/analyze-error",
        { question: turn.question, sql: plan.sql, result_text: resultText },
        { timeoutMs: API_TIMEOUT_MS.longRunningJob },
      );
      setAnalysis({ status: "done", data });
    } catch (error) {
      setAnalysis({ status: "failed", error });
    }
  };

  const applyCorrected = async (corrected: DbAdminChatPlan) => {
    // サーバーがターンの SQL を置き換えて分類し直す（版が進み、確認語からやり直す）。
    const updated = await apiPut<DbAdminChatTurnData>(
      `${conversationPath(conversationId)}/turns/${encodeURIComponent(turn.turn_id)}/plan`,
      { sql: corrected.sql },
    );
    onReplaceTurn(updated);
  };

  const executeButton = (
    <Button
      type="button"
      variant={plan.requires_confirmation ? "danger" : "secondary"}
      size="sm"
      icon={Play}
      loading={running}
      disabled={!canExecute}
      onClick={() => void execute()}
      data-testid={`${TEST_ID}-execute`}
    >
      {execution && !running ? t("chat.execute.again") : t("chat.execute")}
    </Button>
  );
  const hasSql = plan.statements.length > 0;

  return (
    <ChatTurn
      question={turn.question}
      // 会話の持ち主以外（system_admin）が送った質問は、送った人を添える（チャットと同じ。#1928）。
      questionSender={
        turn.sender_display_name == null
          ? undefined
          : t("chat.sentBy", { name: turn.sender_display_name || t("chat.historyOwnerMissing") })
      }
      testId={`${TEST_ID}-turn`}
    >
      <ChatAnswer>
        <ChatProgress
          steps={dbAdminChatProgressSteps({ sentAtMs, generatedAtMs, plan, execution })}
          startedAt={sentAtMs}
          labels={CHAT_PROGRESS_LABELS}
          testId={`${TEST_ID}-progress`}
        />
        {hasSql ? (
          <>
            {turn.corrected ? (
              <p className="text-xs text-fg-muted">{t("dbAdminChat.correctedApplied")}</p>
            ) : null}
            <ChatSqlBlock
              sql={plan.sql}
              testId={`${TEST_ID}-sql`}
              onCopied={() => setCopyError("")}
              onCopyError={setCopyError}
              badges={<PlanBadges plan={plan} executed={executed} />}
              // SELECT は SQL の横の「実行」で実行する（チャットと同じ位置）。確認語の要る SQL は確認語の欄の横。
              actions={!plan.blocked && !plan.requires_confirmation ? executeButton : null}
            />
          </>
        ) : null}
        {turn.explanation ? (
          <MessageText text={turn.explanation} />
        ) : !hasSql ? (
          <p className="text-sm text-fg-muted">{t("dbAdminChat.noSql")}</p>
        ) : null}
        {turn.context_tables.length > 0 ? (
          <p className="break-words text-xs text-fg-muted" data-testid={`${TEST_ID}-context-tables`}>
            {t("dbAdminChat.contextTables", { tables: turn.context_tables.join(", ") })}
          </p>
        ) : null}
        {plan.blocked ? (
          <Banner severity="danger" title={t("dbAdminChat.blocked.title")}>
            <ul className="grid gap-1">
              {plan.blocked_reasons.map((reason) => (
                <li key={reason} className="break-words">
                  {reason}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs">{t("dbAdminChat.blocked.hint")}</p>
          </Banner>
        ) : null}
        {hasSql && !plan.blocked && plan.requires_confirmation ? (
          <>
            <Banner severity="warning">
              <p>{t("dbAdminChat.warning.mutation")}</p>
              {plan.implicit_commit ? <p className="mt-1">{t("dbAdminChat.warning.implicitCommit")}</p> : null}
            </Banner>
            <ExecutionConfirmationField
              id={`${TEST_ID}-confirmation-${turn.turn_id}`}
              value={confirmation}
              onChange={setConfirmation}
              confirmed={dbAdminChatConfirmed(plan, confirmation)}
              expectedLabel={plan.confirmation_target}
              placeholder={plan.confirmation_target}
              helper={t("dbAdminChat.confirmation.helper", { target: plan.confirmation_target })}
              disabled={running}
              actions={executeButton}
            />
          </>
        ) : null}
        {execution ? (
          <section
            aria-label={t("chat.execute.region")}
            className="min-w-0 space-y-3 border-t border-border pt-3"
            data-testid={`${TEST_ID}-execution`}
          >
            {running ? (
              // スピナーは押した「実行」のボタンが出す（同じ処理のスピナーは 1 つ。messaging.md §3.7）。
              <ProcessingIndicator
                active
                label={t("chat.execute.running")}
                operationKey={`db-admin-chat-execute-${turn.turn_id}`}
                placement="result"
                activityIcon="none"
                testId={`${TEST_ID}-execution-running`}
              />
            ) : null}
            {!running && executeError ? (
              <ApiErrorBanner
                error={executeError}
                fallback={t("dbAdminChat.execute.failed")}
                testId={`${TEST_ID}-execution-request-error`}
              />
            ) : null}
            {!running && result?.select_result && executed ? (
              <ChatQueryResult
                results={result.select_result}
                rowLimit={DEFAULT_SQL_ROW_LIMIT}
                elapsedMs={result.timing.elapsed_ms}
                executedAt={new Date(execution.finishedAtMs ?? execution.startedAtMs).toISOString()}
                fullResult={{ hint: t("dbAdminChat.fullResultHint") }}
                testId={`${TEST_ID}-result`}
              />
            ) : null}
            {!running && savedSelect ? (
              // 開き直した会話の SELECT は、前回の実行の要約と「もう一度実行」（行は保存しない。チャットと同じ）。
              <ChatLastExecutionText
                summary={{
                  status: "done",
                  executed_at: savedSelect.executed_at,
                  row_count: savedSelect.row_count ?? 0,
                  column_count: savedSelect.column_count ?? 0,
                  has_more: savedSelect.has_more,
                }}
                testId={`${TEST_ID}-last-execution`}
              />
            ) : null}
            {!running && result && !(result.select_result && executed) && !savedSelect ? (
              // 更新系の結果・失敗は管理 SQL を実行と同じ表示（文ごとの状態・影響行数・commit / rollback）。
              <DbAdminExecutionResult result={result} />
            ) : null}
            <SchemaSyncNotice
              sync={schemaSync}
              testId={`${TEST_ID}-schema-sync-notice`}
              processingTestId={`${TEST_ID}-schema-refresh-processing`}
            />
          </section>
        ) : null}
        {failed && !running ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              icon={Sparkles}
              loading={analysis?.status === "running"}
              onClick={() => void analyze()}
              data-testid={`${TEST_ID}-analyze`}
            >
              {t("dbAdminChat.analyze")}
            </Button>
          </div>
        ) : null}
        {analysis ? (
          <AnalysisPanel analysis={analysis} turnId={turn.turn_id} onUseCorrected={applyCorrected} />
        ) : null}
        {turn.warnings.map((warning) => (
          <Banner key={warning} severity="warning">
            {warning}
          </Banner>
        ))}
        {copyError ? <Banner severity="danger">{copyError}</Banner> : null}
      </ChatAnswer>
    </ChatTurn>
  );
}

function AnalysisPanel({
  analysis,
  turnId,
  onUseCorrected,
}: {
  analysis: AnalysisState;
  turnId: string;
  onUseCorrected: (plan: DbAdminChatPlan) => Promise<void>;
}) {
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<unknown>(null);
  const apply = async (plan: DbAdminChatPlan) => {
    setApplying(true);
    setApplyError(null);
    try {
      await onUseCorrected(plan);
    } catch (error) {
      setApplyError(error);
    } finally {
      setApplying(false);
    }
  };
  return (
    <section
      aria-label={t("dbAdminChat.analyze.title")}
      className="min-w-0 space-y-3 border-t border-border pt-3"
      data-testid={`${TEST_ID}-analysis`}
    >
      {analysis.status === "running" ? (
        <ProcessingIndicator
          active
          label={t("dbAdminChat.analyze.running")}
          operationKey={`db-admin-chat-analyze-${turnId}`}
          placement="result"
          activityIcon="none"
          testId={`${TEST_ID}-analysis-running`}
        />
      ) : null}
      {analysis.status === "failed" ? (
        <ApiErrorBanner error={analysis.error} fallback={t("dbAdminChat.analyze.failed")} />
      ) : null}
      {analysis.status === "done" ? (
        <>
          <p className="text-sm font-medium text-fg">{t("dbAdminChat.analyze.title")}</p>
          <MessageText text={analysis.data.analysis} />
          {analysis.data.corrected_plan && analysis.data.corrected_plan.statements.length > 0 ? (
            <>
              <p className="text-sm font-medium text-fg">{t("dbAdminChat.analyze.corrected")}</p>
              <ChatSqlBlock
                sql={analysis.data.corrected_plan.sql}
                testId={`${TEST_ID}-corrected-sql`}
                badges={<PlanBadges plan={analysis.data.corrected_plan} executed={false} />}
                actions={
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={Check}
                    loading={applying}
                    onClick={() => {
                      if (analysis.data.corrected_plan) void apply(analysis.data.corrected_plan);
                    }}
                    data-testid={`${TEST_ID}-use-corrected`}
                  >
                    {t("dbAdminChat.analyze.useCorrected")}
                  </Button>
                }
              />
              <p className="text-xs text-fg-muted">{t("dbAdminChat.analyze.correctedHint")}</p>
              {applyError ? (
                <ApiErrorBanner error={applyError} fallback={t("dbAdminChat.analyze.useCorrectedFailed")} />
              ) : null}
            </>
          ) : null}
          {analysis.data.warnings.map((warning) => (
            <Banner key={warning} severity="info">
              {warning}
            </Banner>
          ))}
        </>
      ) : null}
    </section>
  );
}
