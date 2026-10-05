import { useId, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Copy,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import {
  ApiErrorBanner,
  Banner,
  Button,
  Card,
  CardContent,
  ChatComposer,
  ChatComposerOption,
  ChatHistoryList,
  ChatLayout,
  ChatProgress,
  ChatSkeleton,
  ChatUserMessage,
  EmptyState,
  MessageText,
  PageBody,
  PageHeader,
  SearchableSelectField,
  SelectField,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  createOptimisticChatMessage,
  useChatProgressTracker,
  toast,
  withOptimisticChatStatus,
  type OptimisticChatMessage,
  useChatAutoScroll,
  useChatHistoryPanel,
} from "@engchina/production-ready-ui";
import {
  useWorkspaceActive,
  useWorkspaceIdentity,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { apiGet, apiPost, isTransportError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { copyTextToClipboard } from "@/lib/clipboard";
import { randomUuid } from "@/lib/randomUuid";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import { useAuth } from "@/features/security/AuthProvider";
import {
  CAPABILITY_PERMISSIONS,
  MENU_PERMISSIONS,
} from "@/features/security/menu-permissions";
import {
  ChatSqlExecutionResult,
  useChatSqlExecution,
} from "./components/ChatSqlExecution";
import { JobFailureBody } from "./components/JobFailureBody";
import {
  useProfileUsageContext,
  useProfileSummaries,
} from "./incrementalQueries";
import {
  CHAT_PROGRESS_LABELS,
  chatJobElapsedMs,
  chatJobProgressSteps,
  chatSubmitProgressSteps,
} from "./chatProgress";
import type { JobCreateData, JobData, Nl2SqlEngine } from "./types";

interface Conversation {
  id: string;
  title: string;
  profile_id: string;
  created_at: string;
}
interface ConversationPage {
  items: Conversation[];
  next_cursor: string | null;
}
interface ConversationData {
  conversation: Conversation;
  turns: JobData[];
}
const inFlight = (job: JobData | undefined) =>
  job?.status === "pending" || job?.status === "running";
/** 会話の取り直しの間隔。 */
const CHAT_POLL_INTERVAL_MS = 1500;
/**
 * 会話の取り直しが成功しないまま、この時間が過ぎたら取り直し直す（#1160）。取り直しは
 * {@link CHAT_POLL_INTERVAL_MS} ごとなので、その数回分。応答しない取得を打ち切って新しく取り直す。
 */
const CHAT_PROGRESS_STALE_AFTER_MS = 10_000;
const errorMessage = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

/** チャットの 1 回の送信の内容。再送信は同じ内容（同じ job ID）で送り直す。 */
interface ChatJobRequest {
  question: string;
  profile_id: string;
  engine: Nl2SqlEngine;
  previous_job_id: string | null;
  /** 送信の前に画面が決める job ID（#900）。 */
  client_job_id: string;
}

/** 送った質問（#907）と、その送信の内容。 */
interface PendingSend {
  message: OptimisticChatMessage;
  request: ChatJobRequest;
}

/**
 * SQL の生成のジョブを投入する。job ID は送信の前に画面が決める（#900）。
 *
 * 投入の応答が上限までに届かない（timeout・通信断）ときも、backend はジョブを作り終えていることが
 * ある。そのときは同じ ID でジョブを取り直し、会話に表示して生成の完了を待つ（二重に送らない）。
 * 再送信も同じ ID で送るので、backend が作り終えていれば作成済みのジョブが返る（二重に生成しない）。
 */
async function submitChatJob(
  request: ChatJobRequest,
): Promise<JobCreateData | JobData> {
  const clientJobId = request.client_job_id;
  try {
    return await apiPost<JobCreateData>(
      "/api/nl2sql/jobs",
      // 生成の prompt には公開版のオントロジーを使い、画面に出さない生成後の接地確認は求めない（#1172）。
      { ...request, generation_only: true, use_ontology_context: true, include_ontology_grounding: false },
      { timeoutMs: API_TIMEOUT_MS.jobSubmit },
    );
  } catch (cause) {
    if (!isTransportError(cause)) throw cause;
    try {
      return await apiGet<JobData>(
        `/api/nl2sql/jobs/${encodeURIComponent(clientJobId)}`,
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail },
      );
    } catch {
      throw cause;
    }
  }
}

/** 送信の応答が届かなかったときは、ジョブが作られている可能性と確かめ方を出す。 */
function sendFailureText(error: unknown) {
  if (!isTransportError(error) || error.kind !== "timeout" || !error.timeoutMs)
    return {};
  return {
    summary: t("chat.sendTimeout", {
      seconds: Math.ceil(error.timeoutMs / 1000),
    }),
    nextAction: t("chat.sendTimeout.action"),
  };
}
const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

/**
 * チャットの生成方法（#890）。SQL 生成画面の実行エンジン（`EngineSelector`）と同じ 3 つで、どれも生成と
 * 安全検査だけを行い SQL を実行しない（Select AI Agent の SQL ツールは SHOWSQL）。
 */
const CHAT_ENGINES: ReadonlyArray<{
  value: Nl2SqlEngine;
  label: string;
  description: string;
}> = [
  {
    value: "select_ai",
    label: t("chat.engine.selectAi"),
    description: t("chat.engine.selectAi.desc"),
  },
  {
    value: "select_ai_agent",
    label: t("chat.engine.selectAiAgent"),
    description: t("chat.engine.selectAiAgent.desc"),
  },
  {
    value: "enterprise_ai_direct",
    label: t("chat.engine.enterprise"),
    description: t("chat.engine.enterprise.desc"),
  },
];

/** 1 往復を永続ジョブにし、前文はサーバーで復元する。送信では SQL を実行しない。 */
export function SqlChatPage() {
  const active = useWorkspaceActive();
  const { hasPermission } = useAuth();
  // 生成した SQL の実行（#1154）は SQL 生成の画面・SELECT SQL の実行と同じ実行の権限が要る。
  const canExecuteSql = hasPermission(CAPABILITY_PERMISSIONS.sqlExecute);
  const canOpenDirectSql = hasPermission(MENU_PERMISSIONS.directSql);
  const identity = useWorkspaceIdentity();
  const queryClient = useQueryClient();
  const [profileId, setProfileId] = useWorkspaceState("profileId", "");
  const [conversationId, setConversationId] = useWorkspaceState(
    "conversationId",
    "",
  );
  const [engine, setEngine] = useWorkspaceState<Nl2SqlEngine>(
    "engine",
    "select_ai",
  );
  const [draft, setDraft] = useWorkspaceState(
    `draft:${profileId}:${conversationId}`,
    "",
  );
  const [historyPanelOpen, setHistoryPanelOpen] = useWorkspaceState(
    "historyOpen",
    false,
  );
  // 開閉の判定・シートの開閉は共通の useChatHistoryPanel（3 製品共通。#1161）。
  const historyPanel = useChatHistoryPanel({
    inlineOpen: historyPanelOpen,
    onInlineOpenChange: setHistoryPanelOpen,
    id: "sql-chat-history",
  });
  const [profileSearch, setProfileSearch] = useState("");
  // 送った質問（#907）。ジョブの投入の応答を待たずに会話の欄の末尾へ出し、投入できたら会話のジョブに置き換える。
  // 投入できなかったときは残して「再送信」を出す。
  const [pendingSend, setPendingSend] = useState<PendingSend | null>(null);
  const pending = pendingSend?.message ?? null;
  const engineDescriptionId = useId();
  const engineOption =
    CHAT_ENGINES.find((option) => option.value === engine) ?? CHAT_ENGINES[0];
  const sendingRef = useRef(false);
  const profiles = useProfileSummaries(profileSearch);
  const profileOptions =
    profiles.data?.pages.flatMap((page) => page.items) ?? [];
  if (!profileId && !profileSearch && profileOptions[0])
    setProfileId(profileOptions[0].id);
  const selectedProfileId = profileId;
  // 最初の読み込みの間だけ欄の形の Skeleton を出す（候補の検索中は欄を出したまま一覧の中で示す）。
  const profilesLoading = profiles.isPending && !profileSearch;
  const detail = useProfileUsageContext(selectedProfileId);
  const selectedProfile =
    detail.data?.profile ??
    profileOptions.find((profile) => profile.id === selectedProfileId);
  const chatKey = [
    "nl2sql",
    "chats",
    identity.owner,
    identity.context,
  ] as const;
  const conversationKey = [...chatKey, conversationId] as const;
  const history = useInfiniteQuery({
    queryKey: chatKey,
    enabled: active,
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) =>
      apiGet<ConversationPage>(
        `/api/nl2sql/chats${pageParam ? `?cursor=${encodeURIComponent(pageParam)}` : ""}`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveList },
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const conversation = useQuery({
    queryKey: conversationKey,
    enabled: active && Boolean(conversationId),
    queryFn: ({ signal }) =>
      apiGet<ConversationData>(
        `/api/nl2sql/chats/${encodeURIComponent(conversationId)}`,
        { signal, timeoutMs: API_TIMEOUT_MS.interactiveDetail },
      ),
    refetchInterval: (query) =>
      active && query.state.data?.turns.some(inFlight)
        ? CHAT_POLL_INTERVAL_MS
        : false,
  });
  // 処理の経過の取り直し（#1160）。応答しない取得（取得中は interval が次を始めない）を打ち切って取り直す。
  const refetchConversation = conversation.refetch;
  const refreshConversation = () =>
    refetchConversation({ cancelRefetch: true, throwOnError: false });
  const turns = conversation.data?.turns ?? [];
  const latest = turns.at(-1);
  const generating = turns.some(inFlight);
  // 応答が届かなかった送信のジョブが、取り直した会話に入っていたら（backend は作り終えていた）、
  // 送った質問の表示を外す（質問を二重に出さず、再送信で二重に生成しない）。
  if (
    pendingSend &&
    turns.some((turn) => turn.job_id === pendingSend.request.client_job_id)
  )
    setPendingSend(null);
  const send = useMutation({
    mutationFn: (request: ChatJobRequest) => submitChatJob(request),
    onSuccess: async (job, { question }) => {
      const id = conversationId || job.job_id;
      // 応答の本文は保存しない。会話 ID と未送信の草稿だけを一時保存する。
      // 草稿は送信の時点で空にしている（投入中に書き始めた次の質問は消さない。#907）。
      setProfileId(selectedProfileId);
      setConversationId(id);
      // 送った質問を、投入できたジョブ（job ID はサーバーと同じ client_job_id）に置き換える。二重に出さない。
      setPendingSend(null);
      queryClient.setQueryData<ConversationData>(
        [...chatKey, id],
        (previous) => ({
          conversation: previous?.conversation ?? {
            id,
            title: question,
            profile_id: selectedProfileId,
            created_at: job.created_at,
          },
          turns: [
            ...(previous?.turns ?? []),
            {
              ...job,
              question,
              profile_id: selectedProfileId,
              conversation_id: id,
              generation_only: true,
            },
          ],
        }),
      );
      await queryClient.invalidateQueries({ queryKey: chatKey });
    },
    onError: () => {
      // 送った質問は残し、理由と「再送信」を出す（入力を失わない。#907）。
      setPendingSend((current) =>
        current
          ? {
              ...current,
              message: withOptimisticChatStatus(current.message, "failed"),
            }
          : current,
      );
      // 応答が届かなくてもジョブが作られていることがある。履歴と会話を取り直して表示に反映する。
      void queryClient.invalidateQueries({ queryKey: chatKey });
    },
    onSettled: () => {
      sendingRef.current = false;
    },
  });
  const stop = useMutation({
    mutationFn: () =>
      apiPost<JobData>(
        `/api/nl2sql/jobs/${turns.find(inFlight)!.job_id}/cancel`,
        {},
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: conversationKey });
    },
  });
  const busy = send.isPending || stop.isPending;
  // 使える業務プロファイルが無いときは、RAG・Agent のチャットと同じく会話の欄を出さない。
  const noProfiles =
    profiles.isSuccess &&
    profileOptions.length === 0 &&
    !profileSearch &&
    !selectedProfile;
  // 業務プロファイルの一覧を読めなかった（最初の読み込み）。上のカードに失敗と再試行を出し、会話の欄は出さない
  // （空の状態を出さない。messaging.md §11.7、#1153）。候補の検索の失敗は欄の中で示す。
  const profilesFailed =
    profiles.isError && !profiles.data && !profileSearch && !selectedProfile;
  // 開いている会話の内容を読み込んでいる（送った質問を出している間は除く）。
  const conversationLoading =
    Boolean(conversationId) && conversation.isPending && !pending;
  // 画面の前提（業務プロファイルの一覧・開いている会話の内容）が揃うまでは、会話の欄に空の状態を出さず
  // 会話の形の Skeleton で覆い、入力欄・生成方法・送信・新しい会話・履歴の開閉を無効にする（#1153）。
  const prerequisitesLoading = profilesLoading || conversationLoading;
  const blocked =
    busy ||
    generating ||
    profilesLoading ||
    !selectedProfile ||
    detail.isError ||
    (Boolean(conversationId) &&
      (conversation.isPending || conversation.isError)) ||
    turns.length >= 50;
  function submit() {
    const question = draft.trim();
    if (!question || blocked || sendingRef.current) return;
    sendingRef.current = true;
    const request: ChatJobRequest = {
      question,
      profile_id: selectedProfileId,
      engine,
      previous_job_id: latest?.job_id ?? null,
      client_job_id: randomUuid(),
    };
    // 送った質問はすぐ会話の欄に出し、入力欄を空にする（#907）。
    setPendingSend({ message: createOptimisticChatMessage(question), request });
    setDraft("");
    // 送った質問は会話の欄の末尾に出す。上を読んでいても末尾へ戻る（messaging.md §11.1）。
    autoScroll.scrollToLatest();
    send.mutate(request);
  }
  /**
   * 送信できなかった質問を、そのままもう一度送る（#907）。同じ job ID・同じ内容で送るので、
   * 応答が届かなかっただけで backend がジョブを作り終えていれば、作成済みのジョブが返る（#900）。
   */
  function resend() {
    if (!pendingSend || blocked || sendingRef.current) return;
    sendingRef.current = true;
    setPendingSend({
      ...pendingSend,
      message: withOptimisticChatStatus(pendingSend.message, "sending"),
    });
    send.mutate(pendingSend.request);
  }
  function resetConversation() {
    if (busy) return;
    setConversationId("");
    setPendingSend(null);
    send.reset();
    stop.reset();
  }
  function openConversation(item: Conversation) {
    if (busy) return;
    setProfileId(item.profile_id);
    setConversationId(item.id);
    historyPanel.closeSheet();
    setPendingSend(null);
    send.reset();
    stop.reset();
  }
  // 会話の欄の自動スクロール（3 製品共通の useChatAutoScroll。#1161）。keep-alive で隠れている間は動かさない。
  // 末尾を見ている間は新しいジョブ・状態の変化に合わせて末尾へ追い、上を読んでいる間は「最新へ」を出す。
  const autoScroll = useChatAutoScroll({
    contentKey: `${turns.length}:${latest?.status ?? ""}:${pending?.localId ?? ""}:${pending?.status ?? ""}`,
    resetKey: `${selectedProfileId}:${conversationId}`,
    enabled: active,
  });
  // 一覧の行・読み込み中・失敗・0 件は 3 製品共通の ChatHistoryList（#1161）。カーソルの API なので「さらに読み込む」。
  const historyContent = (
    <ChatHistoryList
      items={(history.data?.pages ?? [])
        .flatMap((page) => page.items)
        .map((item) => ({
          id: item.id,
          title: item.title,
          meta: dateFormatter.format(new Date(item.created_at)),
        }))}
      currentId={conversationId || null}
      onSelect={(item) => {
        const conversationItem = history.data?.pages
          .flatMap((page) => page.items)
          .find((candidate) => candidate.id === item.id);
        if (conversationItem) openConversation(conversationItem);
      }}
      disabled={busy}
      loading={history.isPending}
      error={history.isError ? history.error : null}
      onRetry={() => void history.refetch()}
      retrying={history.isFetching}
      labels={{
        list: t("chat.history"),
        loading: t("chat.historyLoading"),
        error: t("chat.historyLoadFailed"),
        retry: t("chat.retry"),
        empty: t("chat.historyEmpty"),
      }}
      operationKey="sql-chat-history-load"
      testIds={{
        loading: "sql-chat-history-loading",
        error: "sql-chat-history-error",
        list: "sql-chat-history-list",
      }}
      footer={
        history.hasNextPage ? (
          <Button
            type="button"
            variant="secondary"
            icon={RefreshCw}
            loading={history.isFetchingNextPage}
            onClick={() => void history.fetchNextPage()}
          >
            {t("chat.loadMore")}
          </Button>
        ) : null
      }
    />
  );
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <PageHeader wide title={t("nav.chat")} subtitle={t("chat.subtitle")} />
      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4">
        {/* 対象の業務プロファイル（#891）。RAG の検索・回答プロファイル・Agent の業務 Agent と同じく、
            ページ上部のカードに全幅の SearchableSelectField を 1 つだけ置く（#635）。 */}
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {profilesLoading ? (
              <TimedLoadingState
                label={t("chat.profile.loading")}
                operationKey="sql-chat-profiles-load"
                framed={false}
                testId="sql-chat-profiles-loading"
              >
                {/* ラベルと欄の寸法を予約する。 */}
                <div className="space-y-1.5" aria-hidden>
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-[var(--button-height-md)] w-full" />
                </div>
              </TimedLoadingState>
            ) : profilesFailed ? (
              <ApiErrorBanner
                error={profiles.error}
                fallback={t("chat.profile.loadFailed")}
                testId="sql-chat-profiles-error"
                action={
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={RefreshCw}
                    loading={profiles.isFetching}
                    onClick={() => void profiles.refetch()}
                  >
                    {t("chat.retry")}
                  </Button>
                }
              />
            ) : noProfiles ? (
              <EmptyState
                title={t("chat.noProfiles")}
                hint={t("chat.noProfilesHint")}
              />
            ) : (
              <SearchableSelectField
                id="sql-chat-profile"
                label={t("chat.profile")}
                required
                value={selectedProfileId}
                width="full"
                leadingIcon={Search}
                placeholder={t("chat.profile.placeholder")}
                options={profileOptions.map((profile) => ({
                  value: profile.id,
                  label: profile.name,
                  description: profile.description || undefined,
                  meta: t("chat.profile.objectCount", {
                    count:
                      profile.allowed_table_count + profile.allowed_view_count,
                  }),
                }))}
                selectedOption={
                  selectedProfile
                    ? { value: selectedProfile.id, label: selectedProfile.name }
                    : null
                }
                onQueryChange={setProfileSearch}
                remote={{
                  total: profiles.data?.pages[0]?.total ?? 0,
                  searching:
                    profiles.isFetching && !profiles.isFetchingNextPage,
                  hasMore: Boolean(profiles.hasNextPage),
                  loadingMore: profiles.isFetchingNextPage,
                  onLoadMore: () => void profiles.fetchNextPage(),
                }}
                disabled={busy}
                onValueChange={(id) => {
                  setProfileId(id);
                  resetConversation();
                }}
                labels={{
                  searchPlaceholder: t("chat.profile.searchPlaceholder"),
                  clearSearch: t("common.clearSearch"),
                  count: (shown, total) =>
                    t("chat.profile.count", { shown, total }),
                  noMatch: (query) => t("chat.profile.noMatch", { query }),
                  empty: t("chat.profile.emptyList"),
                  searching: t("chat.profile.searching"),
                  loadMore: t("chat.profile.loadMore"),
                }}
                error={
                  profiles.isError || detail.isError
                    ? errorMessage(
                        profiles.error || detail.error,
                        t("profiles.error.load"),
                      )
                    : undefined
                }
              />
            )}
          </CardContent>
        </Card>
        {noProfiles || profilesFailed ? null : (
          // 骨格（履歴のパネル・シート、会話の領域の上端の行・会話の欄・入力欄の領域）は 3 製品共通の ChatLayout（#1161）。
          <ChatLayout
            history={historyPanel}
            historyTitle={t("chat.history")}
            historyCloseLabel={t("chat.closeHistory")}
            historyContent={historyContent}
            historyToggleDisabled={profilesLoading}
            label={t("chat.conversation")}
            // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す。
            conversationTitle={
              conversationId ? conversation.data?.conversation.title : null
            }
            conversationTitleLoading={
              Boolean(conversationId) && conversation.isPending
            }
            newConversation={{
              label: t("chat.new"),
              onClick: resetConversation,
              disabled: busy || profilesLoading,
            }}
            logLabel={t("chat.messages")}
            logRef={autoScroll.logRef}
            latest={{
              visible: autoScroll.showLatest,
              label: t("chat.latest"),
              onClick: () => autoScroll.scrollToLatest("smooth"),
            }}
            testIdPrefix="sql-chat"
            composer={
              <>
                {/* 入力欄の領域（設定の行・入力欄と送信 / 停止）は 3 製品共通の ChatComposer（#1161）。
                    前提の読み込み中は書けない（書いた文字は作業状態に残す。#1153）。 */}
                <ChatComposer
                  id="sql-chat-composer"
                  value={draft}
                  onValueChange={setDraft}
                  onSubmit={submit}
                  onStop={() => {
                    if (!stop.isPending && !conversation.isError) stop.mutate();
                  }}
                  running={generating}
                  submitBlocked={blocked}
                  disabled={prerequisitesLoading}
                  label={t("chat.query")}
                  placeholder={t("chat.placeholder")}
                  sendLabel={t("chat.send")}
                  stopLabel={t("chat.stop")}
                  maxLength={10000}
                  sendTestId="sql-chat-send"
                  options={
                    // 生成方法（#890）。RAG のチャットの「回答するモデル」と同じく、入力欄の直上の行に
                    // 「ラベル・説明のアイコン・選択」を並べる。ラベルは隣の文言で読めるので欄のラベルは読み上げだけにする。
                    // 選んだ生成方法の説明は常設せず、ラベルの横の info アイコンから出し、選択欄にも説明として結び付ける（#901）。
                    <ChatComposerOption
                      label={t("chat.engine")}
                      labelDecorative
                      info={{
                        label: t("chat.engine.infoLabel"),
                        content: engineOption.description,
                        contentId: engineDescriptionId,
                        contentTestId: "sql-chat-engine-description",
                        testId: "sql-chat-engine-info",
                      }}
                      testId="sql-chat-engine-row"
                    >
                      <SelectField
                        id="sql-chat-engine"
                        label={t("chat.engine")}
                        labelHidden
                        value={engine}
                        size="sm"
                        width="sm"
                        disabled={busy || generating || prerequisitesLoading}
                        onValueChange={setEngine}
                        describedBy={engineDescriptionId}
                        options={CHAT_ENGINES.map(({ value, label }) => ({
                          value,
                          label,
                        }))}
                      />
                    </ChatComposerOption>
                  }
                  footer={
                    <>
                      {stop.isError ? (
                        <ApiErrorBanner
                          error={stop.error}
                          fallback={t("chat.stopFailed")}
                          testId="sql-chat-stop-error"
                        />
                      ) : null}
                      {turns.length >= 50 ? (
                        <Banner severity="info">{t("chat.limit")}</Banner>
                      ) : null}
                    </>
                  }
                />
              </>
            }
          >
            {conversationLoading ? (
              // 会話の内容の読み込み中は文言と経過時間を出し、会話の形の Skeleton で寸法を予約する
              // （3 製品で同じ。messaging.md §3.6 / §11.7）。
              <TimedLoadingState
                label={t("chat.loading")}
                operationKey="sql-chat-conversation-load"
                framed={false}
                testId="sql-chat-conversation-loading"
              >
                <ChatSkeleton />
              </TimedLoadingState>
            ) : profilesLoading ? (
              // 業務プロファイルの一覧の読み込み中は、空の状態（はじめの案内）を出さず会話の形の Skeleton で覆う。
              // 経過時間は上のカードが出しているので重ねない（同じ取得の経過時間は 1 か所。#1153）。
              <ChatSkeleton testId="sql-chat-conversation-skeleton" />
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
            {!conversationId && !pending && !profilesLoading ? (
              <EmptyState
                title={t("chat.empty")}
                hint={t("chat.emptyHint")}
              />
            ) : null}
            {turns.map((turn) => (
              <ChatTurn
                key={turn.job_id}
                turn={turn}
                canExecuteSql={canExecuteSql}
                canOpenDirectSql={canOpenDirectSql}
                tracking={active}
                receivedAt={conversation.dataUpdatedAt}
                refresh={refreshConversation}
              />
            ))}
            {pending ? (
              // 送った質問はジョブの投入の応答を待たずに出す。失敗しても残す（#907）。
              <article
                className="space-y-2"
                data-testid="sql-chat-pending-turn"
              >
                <ChatUserMessage
                  status={pending.status}
                  failedLabel={t("chat.sendFailedStatus")}
                >
                  {pending.content}
                </ChatUserMessage>
                {pending.status === "failed" && send.isError ? (
                  // 失敗の理由は送った質問の直下（会話の中）に出す（messaging.md §10.1 のチャットの扱い）。
                  <ApiErrorBanner
                    error={send.error}
                    fallback={t("chat.sendFailed")}
                    testId="sql-chat-send-error"
                    {...sendFailureText(send.error)}
                    action={
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        icon={RotateCcw}
                        disabled={blocked}
                        onClick={resend}
                      >
                        {t("chat.resend")}
                      </Button>
                    }
                  />
                ) : pending.status === "sending" ? (
                  // 送信の応答（ジョブの投入）を待つ間も段階として出す。投入が遅いと、この段階に
                  // 遅延の案内が付き、生成ではなく送信で待っていることが分かる（#1145）。
                  <Card>
                    <CardContent className="space-y-3">
                      <ChatProgress
                        key={pending.localId}
                        steps={chatSubmitProgressSteps(pending.sentAtMs)}
                        labels={CHAT_PROGRESS_LABELS}
                        testId="sql-chat-progress"
                      />
                    </CardContent>
                  </Card>
                ) : null}
              </article>
            ) : null}
          </ChatLayout>
        )}
      </PageBody>
    </div>
  );
}
function ChatTurn({
  turn,
  canExecuteSql,
  canOpenDirectSql,
  tracking,
  receivedAt,
  refresh,
}: {
  turn: JobData;
  canExecuteSql: boolean;
  canOpenDirectSql: boolean;
  /** 画面が表示されている（keep-alive で隠れていない）。 */
  tracking: boolean;
  /** 会話を最後に取得できた時刻（TanStack Query の dataUpdatedAt）。 */
  receivedAt: number;
  /** 会話を取り直す（応答しない取得は打ち切る）。 */
  refresh: () => Promise<unknown>;
}) {
  const [copyError, setCopyError] = useState("");
  const result = turn.result;
  // 処理の経過の状態を追う（3 製品共通。#1160）。会話の取得が途絶えたら取り直し、終端まで追う。
  const progress = useChatProgressTracker({
    key: turn.job_id,
    steps: chatJobProgressSteps(turn, turn.engine),
    active: inFlight(turn),
    elapsedMs: chatJobElapsedMs(turn),
    receivedAt,
    refresh,
    staleAfterMs: CHAT_PROGRESS_STALE_AFTER_MS,
    enabled: tracking,
  });
  // 実行は明示の操作（送信では実行しない）。安全検査を通った生成 SQL だけを実行できる（#1154）。
  const execution = useChatSqlExecution(turn.job_id);
  const executable = Boolean(
    turn.status === "done" &&
      result?.safety.is_safe &&
      result.generated_sql.trim(),
  );
  const executed = Boolean(execution.data || turn.last_execution);
  return (
    <article className="space-y-2" data-testid="sql-chat-turn">
      <ChatUserMessage>{turn.question || result?.original_question}</ChatUserMessage>
      <Card>
        <CardContent className="space-y-3">
          {/* 処理の段階（#1145）。実行中は今の段階、完了後は回答の上に「処理の経過」の 1 行に畳む。 */}
          <ChatProgress
            {...progress.progressProps}
            labels={CHAT_PROGRESS_LABELS}
            testId="sql-chat-progress"
          />
          {result ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <StatusBadge
                  variant={result.safety.is_safe ? "success" : "danger"}
                  label={
                    !result.safety.is_safe
                      ? t("chat.blocked")
                      : executed
                        ? t("chat.safeExecuted")
                        : t("chat.safe")
                  }
                />
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={Copy}
                    onClick={() => {
                      void copyTextToClipboard(result.generated_sql).then(
                        () => {
                          setCopyError("");
                          toast.success(t("common.action.copied"));
                        },
                        () => setCopyError(t("chat.copyFailed")),
                      );
                    }}
                  >
                    {t("chat.copySql")}
                  </Button>
                  {executable && canExecuteSql ? (
                    // 吹き出しの中の操作なので secondary。実行中はラベルを変えず、アイコンがスピナーになる。
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      icon={Play}
                      loading={execution.running}
                      onClick={execution.run}
                      data-testid="sql-chat-execute"
                    >
                      {executed ? t("chat.execute.again") : t("chat.execute")}
                    </Button>
                  ) : null}
                </div>
              </div>
              <pre className="max-w-full overflow-x-auto rounded-md border border-border bg-canvas p-3 text-sm [scrollbar-gutter:stable]">
                <code>{result.generated_sql}</code>
              </pre>
              {result.explanation ? (
                <MessageText text={result.explanation} />
              ) : null}
              {executable && !canExecuteSql ? (
                <p className="text-xs text-fg-muted">
                  {t("chat.execute.permissionRequired")}
                </p>
              ) : null}
              {executable ? (
                <ChatSqlExecutionResult
                  turn={turn}
                  execution={execution}
                  canOpenDirectSql={canOpenDirectSql}
                />
              ) : null}
            </>
          ) : null}
          {turn.error_message ? (
            <Banner
              severity={turn.error_code === "JOB_CANCELLED" ? "info" : "danger"}
            >
              {turn.error_code === "JOB_CANCELLED" ? (
                t("chat.cancelled")
              ) : (
                <JobFailureBody
                  message={turn.error_message}
                  errorCode={turn.error_code}
                  errorDetail={turn.error_detail}
                />
              )}
            </Banner>
          ) : null}
          {turn.warning_message ? (
            <Banner severity="warning">{turn.warning_message}</Banner>
          ) : null}
          {copyError ? <Banner severity="danger">{copyError}</Banner> : null}
        </CardContent>
      </Card>
    </article>
  );
}
