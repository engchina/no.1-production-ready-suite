import { Check, ChevronDown, Pencil, Plus, SendHorizontal, Square, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import { CitationCard, scoreMaximaForCitations } from "@/components/search/CitationCard";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import type { ChatMessage, ConversationSummary, RetrievedChunk } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { streamChatMessage, type ChatColumn } from "@/lib/chat-stream";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useBusinessViews,
  useCompareModels,
  useConversation,
  useConversations,
  useCreateConversation,
  useUpdateConversation,
} from "@/lib/queries";
import { useQueryClient } from "@tanstack/react-query";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

const COMPARE_MAX = 3;

interface LiveColumn {
  model_id: string;
  label: string;
  answer: string;
  citations: RetrievedChunk[];
  status: "streaming" | "done" | "error";
  traceId: string | null;
  errorMessage: string | null;
  guardrailWarnings: string[];
}

interface LiveTurn {
  user: ChatMessage;
  columns: LiveColumn[];
}

interface Turn {
  user: ChatMessage;
  replies: ChatMessage[];
}

/** メッセージ列を「ユーザー発話 + その回答群」のターンへまとめる。 */
function buildTurns(messages: ChatMessage[]): Turn[] {
  const byReply = new Map<string, ChatMessage[]>();
  for (const message of messages) {
    if (message.role === "ASSISTANT" && message.reply_to_message_id) {
      const list = byReply.get(message.reply_to_message_id) ?? [];
      list.push(message);
      byReply.set(message.reply_to_message_id, list);
    }
  }
  return messages
    .filter((message) => message.role === "USER")
    .map((user) => ({ user, replies: byReply.get(user.message_id) ?? [] }));
}

/** 回答 1 カラム（モデル単位）。ストリーミング中はカーソルを出す。 */
function AssistantColumn({
  label,
  answer,
  citations,
  traceId,
  businessViewId,
  messageId,
  streaming,
  errorMessage,
  guardrailWarnings,
  showLabel,
  className,
}: {
  label: string | null;
  answer: string;
  citations: RetrievedChunk[];
  traceId: string | null;
  businessViewId: string;
  messageId: string | null;
  streaming: boolean;
  errorMessage: string | null;
  guardrailWarnings: string[];
  showLabel: boolean;
  className?: string;
}) {
  const scoreMaxima = useMemo(() => scoreMaximaForCitations(citations), [citations]);
  return (
    <div
      id={messageId ? `message-${messageId}` : undefined}
      className={cn(
        "flex h-full min-w-0 flex-col gap-2 rounded-md border border-border bg-card p-3",
        className
      )}
    >
      {showLabel && label ? (
        <h3
          className="truncate border-b border-border pb-2 text-sm font-semibold text-foreground"
          title={label}
        >
          {label}
        </h3>
      ) : null}
      {errorMessage ? (
        <p className="text-sm text-destructive" role="alert">
          {errorMessage}
        </p>
      ) : (
        <p
          className="whitespace-pre-wrap text-sm leading-relaxed text-foreground"
          aria-live="polite"
        >
          {answer}
          {streaming ? (
            <span className="ml-0.5 inline-block animate-pulse motion-reduce:animate-none">▍</span>
          ) : null}
        </p>
      )}
      {guardrailWarnings.length > 0 ? (
        <Banner severity="warning">
          <span className="font-medium">{t("chat.guardrail")}: </span>
          {guardrailWarnings.join(" / ")}
        </Banner>
      ) : null}
      {!streaming && !errorMessage ? (
        <FeedbackControls
          traceId={traceId}
          businessViewId={businessViewId}
          targetType="answer"
          sourceSurface="chat"
          messageId={messageId}
        />
      ) : null}
      {citations.length > 0 ? (
        <details className="group mt-auto border-t border-border pt-1">
          <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-2 rounded-md px-2 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
            <span>{t("chat.citations.summary", { count: citations.length })}</span>
            <ChevronDown
              className="size-4 shrink-0 text-muted transition-transform duration-200 group-open:rotate-180 motion-reduce:transition-none"
              aria-hidden
            />
          </summary>
          <ul className="space-y-2 pt-2">
            {citations.map((chunk, index) => (
              <CitationCard
                key={chunk.chunk_id}
                chunk={chunk}
                index={index}
                traceId={traceId}
                businessViewId={businessViewId}
                sourceSurface="chat"
                messageId={messageId}
                scoreMaxima={scoreMaxima}
              />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

/** ユーザー発話 + 回答カラム群を 1 ターンとして表示。 */
function MessageTurn({
  user,
  columns,
  businessViewId,
}: {
  user: ChatMessage;
  businessViewId: string;
  columns: {
    key: string;
    label: string | null;
    answer: string;
    citations: RetrievedChunk[];
    traceId: string | null;
    messageId: string | null;
    streaming: boolean;
    errorMessage: string | null;
    guardrailWarnings: string[];
  }[];
}) {
  const compare = columns.length > 1;
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-md bg-primary/10 px-3 py-2 text-sm text-foreground">
          {user.content}
        </div>
      </div>
      {user.guardrail_warnings.length > 0 ? (
        <div className="ml-auto max-w-[85%]">
          <Banner severity="warning">
            <span className="font-medium">{t("chat.guardrail")}: </span>
            {user.guardrail_warnings.join(" / ")}
          </Banner>
        </div>
      ) : null}
      <div
        className="grid grid-cols-1 gap-3"
        style={
          compare
            ? { gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 40rem), 1fr))" }
            : undefined
        }
      >
        {columns.map((column, index) => (
          <AssistantColumn
            key={column.key}
            label={column.label}
            answer={column.answer}
            citations={column.citations}
            traceId={column.traceId}
            businessViewId={businessViewId}
            messageId={column.messageId}
            streaming={column.streaming}
            errorMessage={column.errorMessage}
            guardrailWarnings={column.guardrailWarnings}
            showLabel={compare}
            className={
              compare && columns.length % 2 === 1 && index === columns.length - 1
                ? "col-span-full"
                : undefined
            }
          />
        ))}
      </div>
    </div>
  );
}

export function ChatClient() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const [businessViewId, setBusinessViewId] = useState<string | null>(() =>
    searchParams.get("business_view_id")
  );

  const conversationsQuery = useConversations({
    business_view_id: businessViewId ?? undefined,
    limit: 50,
    offset: 0,
  });
  const conversations = conversationsQuery.data?.items ?? [];

  const [activeId, setActiveId] = useState<string | null>(() =>
    searchParams.get("conversation_id")
  );
  const conversationQuery = useConversation(activeId);
  const persistedMessages = useMemo(
    () => conversationQuery.data?.messages ?? [],
    [conversationQuery.data]
  );

  const createConversation = useCreateConversation();
  const updateConversation = useUpdateConversation();
  const compareModelsQuery = useCompareModels();
  const compareModels = compareModelsQuery.data ?? [];

  const [composer, setComposer] = useState("");
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [liveTurn, setLiveTurn] = useState<LiveTurn | null>(null);
  const [sending, setSending] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [titleError, setTitleError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousBusinessViewIdRef = useRef(businessViewId);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);

  // 業務ビューを切り替えたら会話選択と進行中ストリームをリセットする。
  useEffect(() => {
    if (previousBusinessViewIdRef.current === businessViewId) return;
    previousBusinessViewIdRef.current = businessViewId;
    abortRef.current?.abort();
    setActiveId(null);
    setLiveTurn(null);
    setErrorText("");
    setEditingId(null);
    setTitleError("");
  }, [businessViewId]);

  // メッセージが増えたら末尾までスクロールする。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [persistedMessages.length, liveTurn]);

  useEffect(() => {
    const targetId = location.hash.slice(1);
    if (!targetId || !persistedMessages.length) return;
    const animationFrame = window.requestAnimationFrame(() => {
      document.getElementById(targetId)?.scrollIntoView({ block: "center", behavior: "auto" });
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [location.hash, persistedMessages.length]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    if (editingId) titleInputRef.current?.focus();
  }, [editingId]);

  useEffect(() => {
    if (titleError && !updateConversation.isPending) titleInputRef.current?.focus();
  }, [titleError, updateConversation.isPending]);

  const liveUserMessageId = liveTurn?.user.message_id;
  const turns = useMemo(
    () =>
      buildTurns(persistedMessages).filter(
        (turn) => turn.user.message_id !== liveUserMessageId
      ),
    [persistedMessages, liveUserMessageId]
  );

  function selectConversation(id: string) {
    if (id === activeId) return;
    abortRef.current?.abort();
    setLiveTurn(null);
    setErrorText("");
    setActiveId(id);
  }

  function focusComposer() {
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  async function startNewConversation() {
    if (!businessViewId) return;
    setErrorText("");
    const emptyConversation = conversations.find(
      (conversation) => conversation.status === "ACTIVE" && conversation.message_count === 0
    );
    if (emptyConversation) {
      selectConversation(emptyConversation.id);
      focusComposer();
      return;
    }
    try {
      const created = await createConversation.mutateAsync({ business_view_id: businessViewId });
      setActiveId(created.id);
      setLiveTurn(null);
      focusComposer();
    } catch {
      setErrorText(t("chat.error.send"));
    }
  }

  function startRename(conversation: ConversationSummary) {
    setEditingId(conversation.id);
    setTitleDraft(conversation.title ?? t("chat.sessions.untitled"));
    setTitleError("");
  }

  function cancelRename() {
    setEditingId(null);
    setTitleError("");
  }

  async function saveRename() {
    if (!editingId || updateConversation.isPending) return;
    const title = titleDraft.trim();
    if (!title) {
      setTitleError(t("chat.sessions.renameEmpty"));
      titleInputRef.current?.focus();
      return;
    }
    setTitleError("");
    try {
      await updateConversation.mutateAsync({ id: editingId, payload: { title } });
      setEditingId(null);
    } catch (error) {
      setTitleError(
        error instanceof ApiError
          ? error.messages.join(" / ")
          : t("chat.sessions.renameError")
      );
      titleInputRef.current?.focus();
    }
  }

  function toggleModel(modelId: string) {
    setSelectedModelIds((current) => {
      if (current.includes(modelId)) return current.filter((id) => id !== modelId);
      if (current.length >= COMPARE_MAX) return current;
      return [...current, modelId];
    });
  }

  function updateColumn(modelId: string, patch: Partial<LiveColumn>) {
    setLiveTurn((current) => {
      if (!current) return current;
      return {
        ...current,
        columns: current.columns.map((column) =>
          column.model_id === modelId ? { ...column, ...patch } : column
        ),
      };
    });
  }

  async function send() {
    const content = composer.trim();
    if (!content || !activeId || sending) return;
    setSending(true);
    setErrorText("");
    setComposer("");
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChatMessage(
        activeId,
        { content, model_ids: selectedModelIds },
        {
          onStart: ({ user_message, columns }) => {
            void queryClient.invalidateQueries({ queryKey: ["conversations"] });
            setLiveTurn({
              user: user_message,
              columns: columns.map((column: ChatColumn) => ({
                model_id: column.model_id,
                label: column.label,
                answer: "",
                citations: [],
                status: "streaming",
                traceId: null,
                errorMessage: null,
                guardrailWarnings: [],
              })),
            });
          },
          onDelta: (modelId, text) => {
            setLiveTurn((current) => {
              if (!current) return current;
              return {
                ...current,
                columns: current.columns.map((column) =>
                  column.model_id === modelId
                    ? { ...column, answer: column.answer + text }
                    : column
                ),
              };
            });
          },
          onMetadata: ({ model_id, trace_id, guardrail_warnings }) =>
            updateColumn(model_id, {
              traceId: trace_id,
              guardrailWarnings: guardrail_warnings,
            }),
          onCitations: (modelId, citations) => updateColumn(modelId, { citations }),
          onModelDone: ({ model_id }) => updateColumn(model_id, { status: "done" }),
          onModelError: ({ model_id, message }) =>
            updateColumn(model_id, { status: "error", errorMessage: message }),
          onAllDone: async () => {
            await queryClient.invalidateQueries({ queryKey: ["conversations"] });
            setLiveTurn(null);
          },
        },
        controller.signal
      );
    } catch (error) {
      if (!controller.signal.aborted) {
        setErrorText(error instanceof ApiError ? error.messages.join(" / ") : t("chat.error.send"));
      }
      setLiveTurn(null);
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }

  function stop() {
    abortRef.current?.abort();
    setSending(false);
    setLiveTurn(null);
  }

  const businessViewLoading = businessViewsQuery.isLoading;
  const noBusinessViews = !businessViewLoading && businessViews.length === 0;
  const businessViewOptions: SelectFieldOption[] = [
    { value: "", label: t("chat.businessView.placeholder") },
    ...businessViews.map((view) => ({ value: view.id, label: view.name })),
  ];
  const liveColumns = liveTurn
    ? liveTurn.columns.map((column) => ({
        key: column.model_id || "default",
        label: column.label,
        answer: column.answer,
        citations: column.citations,
        traceId: column.traceId,
        messageId: null,
        streaming: column.status === "streaming",
        errorMessage: column.errorMessage,
        guardrailWarnings: column.guardrailWarnings,
      }))
    : [];

  return (
    <div className="flex min-h-full flex-col lg:h-full lg:min-h-0">
      <PageHeader title={t("chat.title")} subtitle={t("chat.subtitle")} />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-4 sm:p-6 lg:p-8">
        {/* 業務ビュー scope */}
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {businessViewLoading ? (
              <LoadingState rows={1} label={t("chat.businessView.label")} />
            ) : noBusinessViews ? (
              <EmptyState
                title={t("chat.businessView.empty")}
                action={
                  <Button onClick={() => navigate(APP_ROUTES.businessViews)} variant="secondary">
                    {t("chat.businessView.open")}
                  </Button>
                }
              />
            ) : (
              <div className="max-w-md">
                <SelectField
                  id="chat-business-view"
                  label={t("chat.businessView.label")}
                  value={businessViewId ?? ""}
                  options={businessViewOptions}
                  onValueChange={(value) => setBusinessViewId(value || null)}
                />
              </div>
            )}
          </CardContent>
        </Card>

        {businessViewLoading || noBusinessViews ? null : !businessViewId ? (
          <Card className="min-h-0 flex-1">
            <CardContent className="p-4 sm:p-5">
              <EmptyState title={t("chat.businessView.required")} />
            </CardContent>
          </Card>
        ) : (
          <div className="grid min-w-0 gap-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[280px_minmax(0,1fr)]">
            {/* 会話一覧サイドバー */}
            <aside
              aria-label={t("chat.sessions.title")}
              className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-3 shadow-sm lg:min-h-0"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-foreground">
                  {t("chat.sessions.title")}
                </span>
                <Button
                  size="sm"
                  className="h-11 sm:h-8"
                  onClick={() => void startNewConversation()}
                  disabled={createConversation.isPending}
                >
                  <Plus className="size-4" aria-hidden />
                  {t("chat.sessions.new")}
                </Button>
              </div>
              {conversationsQuery.isLoading ? (
                <LoadingState rows={3} label={t("chat.sessions.title")} />
              ) : conversationsQuery.isError ? (
                <ErrorState
                  message={t("chat.sessions.error")}
                  onRetry={() => void conversationsQuery.refetch()}
                />
              ) : conversations.length === 0 ? (
                <p className="px-1 text-sm text-muted">{t("chat.sessions.empty")}</p>
              ) : (
                <ul
                  className="max-h-56 min-h-0 flex-1 space-y-1 overflow-y-auto lg:max-h-none"
                  aria-label={t("chat.sessions.title")}
                >
                  {conversations.map((conversation) => {
                    const title = conversation.title ?? t("chat.sessions.untitled");
                    const errorId = `conversation-title-${conversation.id}-error`;
                    return (
                      <li key={conversation.id} className="group">
                        {editingId === conversation.id ? (
                          <div className="space-y-1 rounded-md bg-primary/5 p-2">
                            <div className="flex items-center gap-1">
                              <label
                                htmlFor={`conversation-title-${conversation.id}`}
                                className="sr-only"
                              >
                                {t("chat.sessions.renameLabel")}
                              </label>
                              <input
                                ref={titleInputRef}
                                id={`conversation-title-${conversation.id}`}
                                value={titleDraft}
                                maxLength={80}
                                disabled={updateConversation.isPending}
                                aria-invalid={titleError ? true : undefined}
                                aria-describedby={titleError ? errorId : undefined}
                                onChange={(event) => setTitleDraft(event.target.value)}
                                onKeyDown={(event) => {
                                  if (event.key === "Enter") {
                                    event.preventDefault();
                                    void saveRename();
                                  } else if (event.key === "Escape") {
                                    event.preventDefault();
                                    cancelRename();
                                  }
                                }}
                                className="h-11 min-w-0 flex-1 rounded-md border border-border bg-background px-2 text-base text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60 sm:h-9 sm:text-sm"
                              />
                              <Button
                                type="button"
                                variant="ghost"
                                size="md"
                                className="h-11 w-11 px-0 sm:h-9 sm:w-9"
                                disabled={updateConversation.isPending}
                                aria-label={t("chat.sessions.renameSave")}
                                onClick={() => void saveRename()}
                              >
                                <Check className="size-4" aria-hidden />
                              </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="md"
                                className="h-11 w-11 px-0 sm:h-9 sm:w-9"
                                disabled={updateConversation.isPending}
                                aria-label={t("chat.sessions.renameCancel")}
                                onClick={cancelRename}
                              >
                                <X className="size-4" aria-hidden />
                              </Button>
                            </div>
                            {titleError ? (
                              <p id={errorId} className="px-1 text-xs text-destructive" role="alert">
                                {titleError}
                              </p>
                            ) : null}
                          </div>
                        ) : (
                          <div
                            className={cn(
                              "grid grid-cols-[minmax(0,1fr)_auto] rounded-md transition-colors",
                              conversation.id === activeId
                                ? "bg-primary/10 text-foreground"
                                : "text-muted hover:bg-muted/30 hover:text-foreground"
                            )}
                          >
                            <button
                              type="button"
                              onClick={() => selectConversation(conversation.id)}
                              aria-current={conversation.id === activeId}
                              className="flex min-w-0 flex-col gap-0.5 rounded-md px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
                            >
                              <span className="truncate font-medium" title={title}>
                                {title}
                              </span>
                              <span className="text-xs tabular-nums text-muted">
                                {t("chat.sessions.metadata", {
                                  count: conversation.message_count,
                                  updatedAt: formatDateTime(conversation.updated_at),
                                })}
                              </span>
                            </button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="md"
                              className={cn(
                                "mr-1 h-11 w-11 self-center px-0 transition-opacity sm:h-9 sm:w-9 sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100",
                                conversation.id === activeId && "sm:opacity-100"
                              )}
                              aria-label={t("chat.sessions.rename", { title })}
                              onClick={() => startRename(conversation)}
                            >
                              <Pencil className="size-4" aria-hidden />
                            </Button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </aside>

            {/* 会話エリア */}
            <section
              aria-label={t("chat.title")}
              className="flex h-[70dvh] min-h-[28rem] min-w-0 flex-col gap-3 overflow-hidden rounded-lg border border-border bg-card shadow-sm lg:h-auto lg:min-h-0"
            >
            <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
              {!activeId ? (
                <EmptyState
                  title={t("chat.composer.selectConversation")}
                  hint={t("chat.messages.empty")}
                />
              ) : conversationQuery.isLoading ? (
                <LoadingState rows={4} label={t("chat.title")} />
              ) : conversationQuery.isError ? (
                <ErrorState
                  message={t("chat.messages.error")}
                  onRetry={() => void conversationQuery.refetch()}
                />
              ) : turns.length === 0 && !liveTurn ? (
                <EmptyState title={t("chat.messages.empty")} />
              ) : (
                <>
                  {turns.map((turn) => (
                    <MessageTurn
                      key={turn.user.message_id}
                      user={turn.user}
                      businessViewId={businessViewId}
                      columns={turn.replies.map((reply) => ({
                        key: reply.message_id,
                        label: reply.model,
                        answer: reply.content,
                        citations: reply.citations,
                        traceId: reply.trace_id,
                        messageId: reply.message_id,
                        streaming: false,
                        errorMessage: reply.status === "ERROR" ? reply.content : null,
                        guardrailWarnings: reply.guardrail_warnings,
                      }))}
                    />
                  ))}
                  {liveTurn ? (
                    <MessageTurn
                      user={liveTurn.user}
                      columns={liveColumns}
                      businessViewId={businessViewId}
                    />
                  ) : null}
                </>
              )}
            </div>

            {/* 比較モデル + composer */}
            <div className="space-y-2 border-t border-border p-3">
              {compareModels.length > 0 ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs font-medium text-muted">{t("chat.compare.label")}</span>
                  {compareModels.map((model) => (
                    <ToggleChip
                      key={model.model_id}
                      selected={selectedModelIds.includes(model.model_id)}
                      onClick={() => toggleModel(model.model_id)}
                    >
                      {model.display_name}
                    </ToggleChip>
                  ))}
                </div>
              ) : null}
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                <label htmlFor="chat-composer" className="sr-only">
                  {t("chat.composer.placeholder")}
                </label>
                <textarea
                  ref={composerRef}
                  id="chat-composer"
                  value={composer}
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  rows={2}
                  placeholder={
                    activeId ? t("chat.composer.placeholder") : t("chat.composer.selectConversation")
                  }
                  disabled={!activeId || sending}
                  className="min-h-11 min-w-0 flex-1 resize-y rounded-md border border-border bg-background p-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60"
                />
                {sending ? (
                  <Button
                    type="button"
                    variant="secondary"
                    className="h-11 w-full shrink-0 sm:h-9 sm:w-auto"
                    onClick={stop}
                    aria-label={t("chat.composer.stop")}
                  >
                    <Square className="size-4" aria-hidden />
                    {t("chat.composer.stop")}
                  </Button>
                ) : (
                  <Button
                    type="button"
                    className="h-11 w-full shrink-0 sm:h-9 sm:w-auto"
                    onClick={() => void send()}
                    disabled={!activeId || composer.trim().length === 0}
                    aria-label={t("chat.composer.send")}
                  >
                    <SendHorizontal className="size-4" aria-hidden />
                    {t("chat.composer.send")}
                  </Button>
                )}
              </div>
              {errorText ? (
                <p className="text-sm text-destructive" role="alert">
                  {errorText}
                </p>
              ) : null}
            </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
