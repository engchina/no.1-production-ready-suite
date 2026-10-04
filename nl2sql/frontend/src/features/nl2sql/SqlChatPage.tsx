import { useEffect, useId, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Copy,
  MessageSquarePlus,
  PanelLeftClose,
  PanelLeftOpen,
  RefreshCw,
  Search,
  SendHorizontal,
} from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  EmptyState,
  FieldActionRow,
  InfoTip,
  ListSkeleton,
  MessageText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  RunStopButton,
  SearchableSelectField,
  SelectField,
  SideSheet,
  Skeleton,
  StatusBadge,
  TextareaField,
  TimedLoadingState,
  isSubmitEnter,
  toast,
} from "@engchina/production-ready-ui";
import {
  useWorkspaceActive,
  useWorkspaceIdentity,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { ApiErrorBanner } from "@/components/ApiErrorBanner";
import { apiGet, apiPost, isTransportError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { copyTextToClipboard } from "@/lib/clipboard";
import { randomUuid } from "@/lib/randomUuid";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";
import {
  useProfileUsageContext,
  useProfileSummaries,
} from "./incrementalQueries";
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
const errorMessage = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

/**
 * SQL の生成のジョブを投入する。job ID は送信の前に画面が決める（#900）。
 *
 * 投入の応答が上限までに届かない（timeout・通信断）ときも、backend はジョブを作り終えていることが
 * ある。そのときは同じ ID でジョブを取り直し、会話に表示して生成の完了を待つ（二重に送らない）。
 */
async function submitChatJob(
  body: Record<string, unknown>,
): Promise<JobCreateData | JobData> {
  const clientJobId = randomUuid();
  try {
    return await apiPost<JobCreateData>(
      "/api/nl2sql/jobs",
      { ...body, client_job_id: clientJobId },
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

function useInlineHistory() {
  const [inline, setInline] = useState(
    () => window.matchMedia("(min-width: 1024px)").matches,
  );
  useEffect(() => {
    const media = window.matchMedia("(min-width: 1024px)");
    const update = () => setInline(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return inline;
}

/** 1 往復を永続ジョブにし、前文はサーバーで復元する。送信では SQL を実行しない。 */
export function SqlChatPage() {
  const active = useWorkspaceActive();
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
  const [historySheetOpen, setHistorySheetOpen] = useState(false);
  const [profileSearch, setProfileSearch] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const inlineHistory = useInlineHistory();
  const [previousInline, setPreviousInline] = useState(inlineHistory);
  if (previousInline !== inlineHistory) {
    setPreviousInline(inlineHistory);
    setHistorySheetOpen(false);
  }
  const historyOpen = inlineHistory ? historyPanelOpen : historySheetOpen;
  const engineDescriptionId = useId();
  const engineOption =
    CHAT_ENGINES.find((option) => option.value === engine) ?? CHAT_ENGINES[0];
  const historyToggleRef = useRef<HTMLButtonElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
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
      active && query.state.data?.turns.some(inFlight) ? 1500 : false,
  });
  const turns = conversation.data?.turns ?? [];
  const latest = turns.at(-1);
  const generating = turns.some(inFlight);
  const send = useMutation({
    mutationFn: (question: string) =>
      submitChatJob({
        question,
        profile_id: selectedProfileId,
        engine,
        generation_only: true,
        previous_job_id: latest?.job_id ?? null,
        use_ontology_context: true,
      }),
    onSuccess: async (job, question) => {
      const id = conversationId || job.job_id;
      // 応答の本文は保存しない。会話 ID と未送信の草稿だけを一時保存する。
      setProfileId(selectedProfileId);
      setConversationId(id);
      setDraft("");
      setSubmittedQuery("");
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
      // 応答が届かなくてもジョブが作られていることがある。履歴と会話を取り直して表示に反映する。
      void queryClient.invalidateQueries({ queryKey: chatKey });
    },
    onSettled: () => {
      sendingRef.current = false;
      setSubmittedQuery("");
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
  const blocked =
    busy ||
    generating ||
    !selectedProfile ||
    detail.isError ||
    (Boolean(conversationId) &&
      (conversation.isPending || conversation.isError)) ||
    turns.length >= 50;
  function submit() {
    const question = draft.trim();
    if (!question || blocked || sendingRef.current) return;
    sendingRef.current = true;
    setSubmittedQuery(question);
    send.mutate(question);
  }
  function resetConversation() {
    if (busy) return;
    setConversationId("");
    setSubmittedQuery("");
    send.reset();
    stop.reset();
  }
  function openConversation(item: Conversation) {
    if (busy) return;
    setProfileId(item.profile_id);
    setConversationId(item.id);
    setHistorySheetOpen(false);
    send.reset();
    stop.reset();
  }
  function toggleHistory() {
    if (inlineHistory) setHistoryPanelOpen(!historyPanelOpen);
    else setHistorySheetOpen(!historySheetOpen);
  }
  useEffect(() => {
    if (active && conversationRef.current)
      conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
  }, [active, conversationId, turns.length, latest?.status, send.isPending]);
  const historyContent = (
    <>
      {history.isPending ? <ListSkeleton rows={3} /> : null}
      {history.isError ? (
        <ApiErrorBanner
          error={history.error}
          fallback={t("chat.loadFailed")}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              icon={RefreshCw}
              onClick={() => void history.refetch()}
            >
              {t("chat.retry")}
            </Button>
          }
        />
      ) : null}
      {history.isSuccess &&
      !history.data.pages.some((page) => page.items.length) ? (
        <EmptyState title={t("chat.historyEmpty")} />
      ) : null}
      <div className="min-h-0 space-y-2 overflow-y-auto [scrollbar-gutter:stable]">
        {history.data?.pages
          .flatMap((page) => page.items)
          .map((item) => (
            <Button
              type="button"
              key={item.id}
              variant="ghost"
              aria-current={item.id === conversationId ? "true" : undefined}
              className="w-full justify-start whitespace-normal text-left"
              disabled={busy}
              onClick={() => openConversation(item)}
            >
              <span className="min-w-0">
                <span className="block break-words">{item.title}</span>
                <span className="block text-xs text-fg-muted">
                  {dateFormatter.format(new Date(item.created_at))}
                </span>
              </span>
            </Button>
          ))}
        {history.hasNextPage ? (
          <Button
            type="button"
            variant="secondary"
            icon={RefreshCw}
            loading={history.isFetchingNextPage}
            onClick={() => void history.fetchNextPage()}
          >
            {t("chat.loadMore")}
          </Button>
        ) : null}
      </div>
    </>
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
        {noProfiles ? null : (
          <div
            className={`grid min-h-0 min-w-0 flex-1 gap-4 ${inlineHistory && historyPanelOpen ? "lg:grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)]" : ""}`}
          >
            {inlineHistory ? (
              // lg 以上: 開くとチャットの左に並べ、チャットの幅が縮む。閉じている間も描いて aria-controls の先を保つ。
              <aside
                id="sql-chat-history"
                aria-label={t("chat.history")}
                data-testid="sql-chat-history"
                className={
                  historyPanelOpen
                    ? "flex min-h-0 min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface px-3 pb-3 pt-2 shadow-sm"
                    : "hidden"
                }
              >
                {/* 見出しの行はチャットの上端の行と高さをそろえる。 */}
                <h2 className="flex min-h-8 items-center px-1 text-sm font-medium text-fg">
                  {t("chat.history")}
                </h2>
                {historyContent}
              </aside>
            ) : (
              // lg 未満: 本文の上に重ねるモーダルの side sheet（会話を選ぶ・Esc・scrim・閉じるボタンで閉じる）。
              <SideSheet
                open={historySheetOpen}
                onClose={() => setHistorySheetOpen(false)}
                title={t("chat.history")}
                closeLabel={t("chat.closeHistory")}
                id="sql-chat-history"
                returnFocusRef={historyToggleRef}
                bodyClassName="gap-3"
                data-testid="sql-chat-history"
              >
                {historyContent}
              </SideSheet>
            )}
            <section
              aria-label={t("chat.conversation")}
              className="flex min-h-0 min-w-0 flex-col rounded-lg border border-border bg-surface shadow-sm"
              data-testid="sql-chat-panel"
            >
              {/* 上端の行: 会話の履歴の開閉（左端）・今の会話の名前・新しい会話（RAG のチャットと同じ。#664 / #889）。 */}
              <div className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
                <Button
                  type="button"
                  ref={historyToggleRef}
                  variant="ghost"
                  size="sm"
                  iconOnly
                  icon={historyOpen ? PanelLeftClose : PanelLeftOpen}
                  aria-label={t("chat.history")}
                  aria-controls="sql-chat-history"
                  aria-expanded={historyOpen}
                  data-testid="sql-chat-history-toggle"
                  onClick={toggleHistory}
                />
                {/* 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す。 */}
                <div className="min-w-0 flex-1">
                  {!conversationId ? null : conversation.data ? (
                    <h2
                      className="truncate text-sm font-medium text-fg"
                      title={conversation.data.conversation.title}
                      data-testid="sql-chat-conversation-title"
                    >
                      {conversation.data.conversation.title}
                    </h2>
                  ) : conversation.isPending ? (
                    <Skeleton className="h-4 w-40" />
                  ) : null}
                </div>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  icon={MessageSquarePlus}
                  disabled={busy}
                  onClick={resetConversation}
                >
                  {t("chat.new")}
                </Button>
              </div>
              <div
                ref={conversationRef}
                className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4 [scrollbar-gutter:stable]"
                data-testid="sql-chat-conversation"
              >
                {conversationId && conversation.isPending ? (
                  <ProcessingIndicator
                    active
                    label={t("chat.loading")}
                    placement="panel"
                  />
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
                {!conversationId && !send.isPending ? (
                  <EmptyState
                    title={t("chat.empty")}
                    hint={t("chat.emptyHint")}
                  />
                ) : null}
                {turns.map((turn) => (
                  <ChatTurn key={turn.job_id} turn={turn} />
                ))}
                {send.isPending && submittedQuery ? (
                  <div className="space-y-2">
                    <div className="ml-auto w-fit max-w-full rounded-md bg-accent-subtle px-3 py-2 break-words">
                      {submittedQuery}
                    </div>
                    <ProcessingIndicator
                      active
                      label={t("chat.generating")}
                      placement="panel"
                    />
                  </div>
                ) : null}
              </div>
              <div
                className="shrink-0 space-y-2 border-t border-border p-3"
                data-testid="sql-chat-composer-region"
              >
                {/* 生成方法（#890）。RAG のチャットの「回答するモデル」と同じく、入力欄の直上の行に
                    「ラベル・説明のアイコン・選択」を並べる。ラベルは隣の文言で読めるので欄のラベルは読み上げだけにする。
                    選んだ生成方法の説明は常設せず、ラベルの横の info アイコンから出し、選択欄にも説明として結び付ける（#901）。 */}
                <div
                  className="flex flex-wrap items-center gap-2"
                  data-testid="sql-chat-engine-row"
                >
                  <span className="inline-flex items-center gap-0.5">
                    <span
                      className="text-xs font-medium text-fg-muted"
                      aria-hidden="true"
                    >
                      {t("chat.engine")}
                    </span>
                    <InfoTip
                      label={t("chat.engine.infoLabel")}
                      content={engineOption.description}
                      contentId={engineDescriptionId}
                      contentTestId="sql-chat-engine-description"
                      data-testid="sql-chat-engine-info"
                    />
                  </span>
                  <SelectField
                    id="sql-chat-engine"
                    label={t("chat.engine")}
                    labelHidden
                    value={engine}
                    size="sm"
                    width="sm"
                    disabled={busy || generating}
                    onValueChange={setEngine}
                    describedBy={engineDescriptionId}
                    options={CHAT_ENGINES.map(({ value, label }) => ({
                      value,
                      label,
                    }))}
                  />
                </div>
                <FieldActionRow
                  actions={
                    <RunStopButton
                      running={generating}
                      onRun={submit}
                      onStop={() => {
                        if (!stop.isPending && !conversation.isError)
                          stop.mutate();
                      }}
                      runLabel={t("chat.send")}
                      stopLabel={t("chat.stop")}
                      runIcon={SendHorizontal}
                      runDisabled={!draft.trim() || blocked}
                      size="lg"
                      testId="sql-chat-send"
                    />
                  }
                >
                  <TextareaField
                    id="sql-chat-composer"
                    label={t("chat.query")}
                    labelHidden
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (isSubmitEnter(event) && !event.shiftKey) {
                        event.preventDefault();
                        submit();
                      }
                    }}
                    rows={2}
                    maxLength={10000}
                    placeholder={t("chat.placeholder")}
                    className="space-y-0"
                  />
                </FieldActionRow>
                {send.isError ? (
                  <ApiErrorBanner
                    error={send.error}
                    fallback={t("chat.sendFailed")}
                    testId="sql-chat-send-error"
                    {...sendFailureText(send.error)}
                  />
                ) : null}
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
              </div>
            </section>
          </div>
        )}
      </PageBody>
    </div>
  );
}
function ChatTurn({ turn }: { turn: JobData }) {
  const [copyError, setCopyError] = useState("");
  const result = turn.result;
  return (
    <article className="space-y-2" data-testid="sql-chat-turn">
      <div className="ml-auto w-fit max-w-full rounded-md bg-accent-subtle px-3 py-2 break-words">
        {turn.question || result?.original_question}
      </div>
      <Card>
        <CardContent className="space-y-3">
          {inFlight(turn) ? (
            <ProcessingIndicator
              active
              label={t("chat.generating")}
              operationKey={turn.job_id}
              startedAt={turn.started_at || turn.created_at}
              placement="panel"
            />
          ) : null}
          {result ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <StatusBadge
                  variant={result.safety.is_safe ? "success" : "danger"}
                  label={
                    result.safety.is_safe ? t("chat.safe") : t("chat.blocked")
                  }
                />
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
              </div>
              <pre className="max-w-full overflow-x-auto rounded-md border border-border bg-canvas p-3 text-sm [scrollbar-gutter:stable]">
                <code>{result.generated_sql}</code>
              </pre>
              {result.explanation ? (
                <MessageText text={result.explanation} />
              ) : null}
            </>
          ) : null}
          {turn.error_message ? (
            <Banner
              severity={turn.error_code === "JOB_CANCELLED" ? "info" : "danger"}
            >
              {turn.error_code === "JOB_CANCELLED"
                ? t("chat.cancelled")
                : turn.error_message}
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
