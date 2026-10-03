import { useEffect, useRef, useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Copy,
  History,
  MessageSquarePlus,
  RefreshCw,
  SendHorizontal,
} from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  EmptyState,
  FieldActionRow,
  ListSkeleton,
  MessageText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  RunStopButton,
  SearchableSelectField,
  SelectField,
  SideSheet,
  StatusBadge,
  TextareaField,
  isSubmitEnter,
  toast,
} from "@engchina/production-ready-ui";
import {
  useWorkspaceActive,
  useWorkspaceIdentity,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { apiGet, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { copyTextToClipboard } from "@/lib/clipboard";
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
const dateFormatter = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

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
  const historyToggleRef = useRef<HTMLButtonElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const sendingRef = useRef(false);
  const profiles = useProfileSummaries(profileSearch);
  const profileOptions =
    profiles.data?.pages.flatMap((page) => page.items) ?? [];
  if (!profileId && !profileSearch && profileOptions[0])
    setProfileId(profileOptions[0].id);
  const selectedProfileId = profileId;
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
      apiPost<JobCreateData>(
        "/api/nl2sql/jobs",
        {
          question,
          profile_id: selectedProfileId,
          engine,
          generation_only: true,
          previous_job_id: latest?.job_id ?? null,
          use_ontology_context: true,
        },
        { timeoutMs: API_TIMEOUT_MS.interactiveDetail },
      ),
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
        <Banner severity="danger">
          {errorMessage(history.error, t("chat.loadFailed"))}
          <Button
            type="button"
            variant="secondary"
            size="sm"
            icon={RefreshCw}
            onClick={() => void history.refetch()}
          >
            {t("chat.retry")}
          </Button>
        </Banner>
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
        <Card>
          <CardContent className="flex flex-col gap-4 md:flex-row md:items-end">
            <SearchableSelectField
              id="sql-chat-profile"
              label={t("chat.profile")}
              value={selectedProfileId}
              width="full"
              className="min-w-0 flex-1"
              options={profileOptions.map((profile) => ({
                value: profile.id,
                label: profile.name,
                description: profile.description,
              }))}
              selectedOption={
                selectedProfile
                  ? { value: selectedProfile.id, label: selectedProfile.name }
                  : null
              }
              onQueryChange={setProfileSearch}
              remote={{
                total: profiles.data?.pages[0]?.total ?? 0,
                searching: profiles.isFetching && !profiles.isFetchingNextPage,
                hasMore: Boolean(profiles.hasNextPage),
                loadingMore: profiles.isFetchingNextPage,
                onLoadMore: () => void profiles.fetchNextPage(),
              }}
              disabled={busy}
              onValueChange={(id) => {
                setProfileId(id);
                resetConversation();
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
            <SelectField
              id="sql-chat-engine"
              label={t("chat.engine")}
              value={engine}
              width="sm"
              disabled={busy || generating}
              onValueChange={setEngine}
              options={[
                { value: "select_ai", label: t("chat.engine.selectAi") },
                {
                  value: "enterprise_ai_direct",
                  label: t("chat.engine.enterprise"),
                },
              ]}
            />
          </CardContent>
        </Card>
        {profiles.isSuccess &&
        profileOptions.length === 0 &&
        !profileSearch &&
        !selectedProfile ? (
          <EmptyState
            title={t("chat.noProfiles")}
            hint={t("chat.noProfilesHint")}
          />
        ) : null}
        <div
          className={`grid min-h-0 min-w-0 flex-1 gap-4 ${inlineHistory && historyPanelOpen ? "lg:grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)]" : ""}`}
        >
          {inlineHistory ? (
            <aside
              id="sql-chat-history"
              aria-label={t("chat.history")}
              data-testid="sql-chat-history"
              className={
                historyPanelOpen
                  ? "flex min-h-0 min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface p-3 shadow-sm"
                  : "hidden"
              }
            >
              <h2 className="text-sm font-medium">{t("chat.history")}</h2>
              {historyContent}
            </aside>
          ) : (
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
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
              <h2 className="min-w-0 flex-1 basis-full truncate text-sm font-medium md:basis-auto">
                {conversation.data?.conversation.title || t("chat.new")}
              </h2>
              <div className="flex shrink-0 gap-2">
                <Button
                  type="button"
                  ref={historyToggleRef}
                  variant="secondary"
                  size="sm"
                  icon={History}
                  aria-controls="sql-chat-history"
                  aria-expanded={historyOpen}
                  onClick={toggleHistory}
                >
                  {t("chat.history")}
                </Button>
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
                <Banner severity="danger">
                  {errorMessage(conversation.error, t("chat.loadFailed"))}
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    icon={RefreshCw}
                    onClick={() => void conversation.refetch()}
                  >
                    {t("chat.retry")}
                  </Button>
                </Banner>
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
                <Banner severity="danger">
                  {errorMessage(send.error, t("chat.sendFailed"))}
                </Banner>
              ) : null}
              {stop.isError ? (
                <Banner severity="danger">
                  {errorMessage(stop.error, t("chat.stopFailed"))}
                </Banner>
              ) : null}
              {turns.length >= 50 ? (
                <Banner severity="info">{t("chat.limit")}</Banner>
              ) : null}
            </div>
          </section>
        </div>
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
