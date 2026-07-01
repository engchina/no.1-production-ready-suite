import { ChevronDown, Plus, SendHorizontal, Square } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { PageHeader } from "@/components/PageHeader";
import { CitationCard, scoreMaximaForCitations } from "@/components/search/CitationCard";
import { EmptyState, ErrorState, LoadingState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { ToggleChip } from "@/components/ui/toggle-chip";
import type { ChatMessage, RetrievedChunk } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { streamChatMessage, type ChatColumn } from "@/lib/chat-stream";
import { t } from "@/lib/i18n";
import {
  useBusinessViews,
  useCompareModels,
  useConversation,
  useConversations,
  useCreateConversation,
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
  streaming,
  errorMessage,
  showLabel,
  className,
}: {
  label: string | null;
  answer: string;
  citations: RetrievedChunk[];
  traceId: string | null;
  streaming: boolean;
  errorMessage: string | null;
  showLabel: boolean;
  className?: string;
}) {
  const scoreMaxima = useMemo(() => scoreMaximaForCitations(citations), [citations]);
  return (
    <div
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
          className="max-w-prose whitespace-pre-wrap text-sm leading-relaxed text-foreground"
          aria-live="polite"
        >
          {answer}
          {streaming ? (
            <span className="ml-0.5 inline-block animate-pulse motion-reduce:animate-none">▍</span>
          ) : null}
        </p>
      )}
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
}: {
  user: ChatMessage;
  columns: {
    key: string;
    label: string | null;
    answer: string;
    citations: RetrievedChunk[];
    traceId: string | null;
    streaming: boolean;
    errorMessage: string | null;
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
            streaming={column.streaming}
            errorMessage={column.errorMessage}
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
  const queryClient = useQueryClient();

  const businessViewsQuery = useBusinessViews({ status: "ACTIVE", limit: 50, offset: 0 });
  const businessViews = businessViewsQuery.data?.items ?? [];
  const [businessViewId, setBusinessViewId] = useState<string | null>(null);

  const conversationsQuery = useConversations({
    business_view_id: businessViewId ?? undefined,
    limit: 50,
    offset: 0,
  });
  const conversations = conversationsQuery.data?.items ?? [];

  const [activeId, setActiveId] = useState<string | null>(null);
  const conversationQuery = useConversation(activeId);
  const persistedMessages = useMemo(
    () => conversationQuery.data?.messages ?? [],
    [conversationQuery.data]
  );

  const createConversation = useCreateConversation();
  const compareModelsQuery = useCompareModels();
  const compareModels = compareModelsQuery.data ?? [];

  const [composer, setComposer] = useState("");
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [liveTurn, setLiveTurn] = useState<LiveTurn | null>(null);
  const [sending, setSending] = useState(false);
  const [errorText, setErrorText] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 業務ビューを切り替えたら会話選択と進行中ストリームをリセットする。
  useEffect(() => {
    abortRef.current?.abort();
    setActiveId(null);
    setLiveTurn(null);
    setErrorText("");
  }, [businessViewId]);

  // メッセージが増えたら末尾までスクロールする。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [persistedMessages.length, liveTurn]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const turns = useMemo(() => buildTurns(persistedMessages), [persistedMessages]);

  function selectConversation(id: string) {
    if (id === activeId) return;
    abortRef.current?.abort();
    setLiveTurn(null);
    setErrorText("");
    setActiveId(id);
  }

  async function startNewConversation() {
    if (!businessViewId) return;
    setErrorText("");
    try {
      const created = await createConversation.mutateAsync({ business_view_id: businessViewId });
      setActiveId(created.id);
      setLiveTurn(null);
    } catch {
      setErrorText(t("chat.error.send"));
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
          onMetadata: ({ model_id, trace_id }) => updateColumn(model_id, { traceId: trace_id }),
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
        streaming: column.status === "streaming",
        errorMessage: column.errorMessage,
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
                  {conversations.map((conversation) => (
                    <li key={conversation.id}>
                      <button
                        type="button"
                        onClick={() => selectConversation(conversation.id)}
                        aria-current={conversation.id === activeId}
                        className={cn(
                          "flex w-full flex-col gap-0.5 rounded-md px-3 py-2 text-left text-sm transition-colors",
                          conversation.id === activeId
                            ? "bg-primary/10 text-foreground"
                            : "text-muted hover:bg-muted/30 hover:text-foreground"
                        )}
                      >
                        <span className="truncate font-medium">
                          {conversation.title ?? t("chat.sessions.untitled")}
                        </span>
                        <span className="text-xs text-muted">
                          {t("chat.sessions.messageCount", {
                            count: conversation.message_count,
                          })}
                        </span>
                      </button>
                    </li>
                  ))}
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
                      columns={turn.replies.map((reply) => ({
                        key: reply.message_id,
                        label: reply.model,
                        answer: reply.content,
                        citations: reply.citations,
                        traceId: reply.trace_id,
                        streaming: false,
                        errorMessage: reply.status === "ERROR" ? reply.content : null,
                      }))}
                    />
                  ))}
                  {liveTurn ? <MessageTurn user={liveTurn.user} columns={liveColumns} /> : null}
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
