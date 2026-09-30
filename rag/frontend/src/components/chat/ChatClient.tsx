import {
  Disclosure,
  PageBody,
  PageHeader,
  Button,
  Banner,
  Card,
  CardContent,
  SelectField,
  type SelectFieldOption,
  TextareaField,
  TextField,
  ToggleChip,
  TimedLoadingState,
  Skeleton,
  ListSkeleton,
  DEFAULT_PAGE_SIZE,
  INFORMATION_LIST_ROW_CLASS,
  INFORMATION_LIST_SCROLL_CLASS,
  offsetForPage,
  offsetPagination,
  toast,
  useConfirm,
} from "@engchina/production-ready-ui";
import {
  Check,
  Pencil,
  Plus,
  RotateCcw,
  SendHorizontal,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import { ListPagination } from "@/components/ListPagination";
import { RunStopButton } from "@/components/RunStopButton";
import { CitationCard } from "@/components/search/CitationCard";
import { AnswerProgress } from "@/components/search/AnswerProgress";
import { SavedDocragAnswer } from "@/components/search/SavedDocragAnswer";
import { DocragAnswerPanel } from "@/components/search/DocragAnswerPanel";
import { useAuth } from "@/components/security/AuthProvider";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { isSubmitEnter } from "@/lib/keyboard";
import type { ChatMessage, ConversationSummary, RetrievedChunk } from "@/lib/api";
import { ApiError } from "@/lib/api";
import type { AnswerStageEvent } from "@/lib/answer-progress";
import { streamChatMessage, type ChatColumn } from "@/lib/chat-stream";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { isNullableString, useWorkspaceState } from "@/lib/workspace-state";
import {
  useBusinessViews,
  useCompareModels,
  useConversation,
  useConversations,
  useCreateConversation,
  useSavedDocragTraceIds,
  useUpdateConversation,
  useDeleteConversation,
} from "@/lib/queries";
import { useQueryClient } from "@tanstack/react-query";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

const COMPARE_MAX = 3;
const EMPTY_TRACE_IDS: ReadonlySet<string> = new Set();

/** 会話一覧のページ（業務ビューごと。#403）。別の業務ビューに移ったら 1 ページ目から。 */
interface ConversationsPage {
  businessViewId: string | null;
  offset: number;
}

function isConversationsPage(value: unknown): value is ConversationsPage {
  const page = value as ConversationsPage;
  return (
    typeof page === "object" &&
    page !== null &&
    isNullableString(page.businessViewId) &&
    Number.isInteger(page.offset) &&
    page.offset >= 0
  );
}

interface LiveColumn {
  model_id: string;
  label: string;
  answer: string;
  citations: RetrievedChunk[];
  status: "streaming" | "done" | "error";
  traceId: string | null;
  errorMessage: string | null;
  guardrailWarnings: string[];
  /** 回答の根拠・実行記録(無い回答では null)。 */
  docrag: unknown;
  /** 回答生成の工程の進捗（#375）。 */
  stages: AnswerStageEvent[];
  startedAtMs: number;
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
  docrag = null,
  savedDocrag = false,
  progress = null,
  onRetry,
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
  docrag?: unknown;
  /** 保存された回答がある(trace_id から根拠と実行記録を開ける)。 */
  savedDocrag?: boolean;
  /** 生成中の工程と開始時刻（#375）。回答の本文が届くまで経過時間と今の工程を出す。 */
  progress?: { stages: AnswerStageEvent[]; startedAtMs: number } | null;
  /** 失敗した回答をもう一度送信する（最新の質問だけ）。 */
  onRetry?: () => void;
  showLabel: boolean;
  className?: string;
}) {
  const waitingForAnswer = streaming && !answer && !errorMessage && progress !== null;
  return (
    <div
      id={messageId ? `message-${messageId}` : undefined}
      className={cn(
        "flex h-full min-w-0 flex-col gap-2 rounded-md border border-border bg-surface p-3",
        className
      )}
    >
      {showLabel && label ? (
        <h3
          className="truncate border-b border-border pb-2 text-sm font-semibold text-fg"
          title={label}
        >
          {label}
        </h3>
      ) : null}
      {errorMessage ? (
        <Banner
          severity="danger"
          action={
            onRetry ? (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-11 sm:h-8"
                icon={RotateCcw}
                onClick={onRetry}
              >
                {t("chat.error.retry")}
              </Button>
            ) : undefined
          }
        >
          {errorMessage}
        </Banner>
      ) : waitingForAnswer && progress ? (
        // 回答の本文は生成と検査が終わってからまとめて届く。それまでは今の工程と経過時間を出す。
        <AnswerProgress
          active
          stages={progress.stages}
          startedAtMs={progress.startedAtMs}
          testId="chat-answer-progress"
        />
      ) : (
        <p
          className="whitespace-pre-wrap text-sm leading-relaxed text-fg"
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
      {!streaming && !errorMessage && docrag ? <DocragAnswerPanel docrag={docrag} traceId={traceId} /> : null}
      {!streaming && !errorMessage && !docrag && savedDocrag && traceId ? (
        <Disclosure
          variant="plain"
          summary={t("chat.docrag.open")}
          className="border-t border-border px-2 pt-1"
        >
          <SavedDocragAnswer traceId={traceId} businessViewId={businessViewId} showAnswer={false} />
        </Disclosure>
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
        <Disclosure
          variant="plain"
          summary={t("chat.citations.summary", { count: citations.length })}
          className="mt-auto border-t border-border px-2 pt-1"
        >
          <ul className="space-y-2">
            {citations.map((chunk, index) => (
              <CitationCard
                key={chunk.chunk_id}
                chunk={chunk}
                index={index}
                traceId={traceId}
                businessViewId={businessViewId}
                sourceSurface="chat"
                messageId={messageId}
              />
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}

/** ユーザー発話 + 回答カラム群を 1 ターンとして表示。 */
function MessageTurn({
  user,
  columns,
  businessViewId,
  onRetry,
}: {
  user: ChatMessage;
  businessViewId: string;
  /** 失敗した回答があるときに同じ質問をもう一度送る（最新のターンだけ渡す）。 */
  onRetry?: () => void;
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
    docrag?: unknown;
    savedDocrag?: boolean;
    progress?: { stages: AnswerStageEvent[]; startedAtMs: number } | null;
  }[];
}) {
  const compare = columns.length > 1;
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-md bg-accent-subtle px-3 py-2 text-sm text-fg">
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
            docrag={column.docrag}
            savedDocrag={column.savedDocrag}
            progress={column.progress}
            onRetry={column.errorMessage ? onRetry : undefined}
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
  // 選択中の業務ビュー・会話・入力中の下書きは、ページを行き来しても再読込しても残す
  // （workspace-state.md）。URL の deep-link があればそちらを優先する。
  const urlBusinessViewId = searchParams.get("business_view_id");
  const urlConversationId = searchParams.get("conversation_id");
  const [businessViewId, setBusinessViewId] = useWorkspaceState<string | null>(
    "chat.businessViewId",
    null,
    isNullableString,
    urlBusinessViewId ?? undefined
  );
  // 復元した業務ビューがアーカイブ・削除済みなら選択を外す（別の業務ビューへ置き換えない）。
  const businessViewMissing =
    Boolean(businessViewId) &&
    Boolean(businessViewsQuery.data) &&
    !businessViewsQuery.data?.has_next &&
    !businessViews.some((view) => view.id === businessViewId);
  useEffect(() => {
    if (businessViewMissing) setBusinessViewId(null);
  }, [businessViewMissing, setBusinessViewId]);
  // 参照 KB が 0 件の業務ビューではチャットしない（利用者の全 KB を検索しない。#304）。
  // backend も送信時に 409 で理由を返すが、送信する前にこの場で理由を示す。
  const canOpenBusinessViews = useAuth().hasPermission(MENU_PERMISSIONS.businessViews);
  const selectedBusinessView = businessViews.find((view) => view.id === businessViewId);
  const businessViewWithoutKnowledgeBases = selectedBusinessView?.knowledge_base_count === 0;
  // 会話一覧はサーバー側でページングする（50 件で打ち切らない。#403）。
  // ページは作業状態として残す（workspace-state.md）。別の業務ビューに移ったら 1 ページ目から。
  const [conversationsPage, setConversationsPage] = useWorkspaceState<ConversationsPage>(
    "chat.conversationsPage",
    { businessViewId, offset: 0 },
    isConversationsPage
  );
  const conversationOffset =
    conversationsPage.businessViewId === businessViewId ? conversationsPage.offset : 0;
  const setConversationOffset = (offset: number) =>
    setConversationsPage({ businessViewId, offset });
  const conversationsQuery = useConversations({
    business_view_id: businessViewId ?? undefined,
    limit: DEFAULT_PAGE_SIZE,
    offset: conversationOffset,
  });
  const conversations = conversationsQuery.data?.items ?? [];
  // 会話が減って今のページが空になったら、最後のページへ戻す（空の案内を出さない）。
  const conversationsData = conversationsQuery.data;
  const conversationsOutOfRange = Boolean(
    conversationsData &&
      conversationsData.offset === conversationOffset &&
      conversationsData.items.length === 0 &&
      conversationOffset > 0
  );
  const lastConversationsOffset =
    conversationsData && conversationsData.total > 0
      ? offsetForPage(Math.ceil(conversationsData.total / DEFAULT_PAGE_SIZE), DEFAULT_PAGE_SIZE)
      : 0;
  if (conversationsOutOfRange && lastConversationsOffset !== conversationOffset) {
    setConversationOffset(lastConversationsOffset);
  }

  const [activeId, setActiveId] = useWorkspaceState<string | null>(
    "chat.conversationId",
    null,
    isNullableString,
    urlConversationId ?? (urlBusinessViewId ? null : undefined)
  );
  const conversationQuery = useConversation(activeId);
  const persistedMessages = useMemo(
    () => conversationQuery.data?.messages ?? [],
    [conversationQuery.data]
  );
  // 保存された回答がある回答(trace_id)。該当する回答だけ根拠と実行記録を開ける。
  // 回答履歴のページングに依存しないよう、開いている会話の回答の trace_id で引き当てる（#304）。
  const replyTraceIds = useMemo(
    () =>
      persistedMessages
        .filter((message) => message.role === "ASSISTANT" && message.trace_id)
        .map((message) => message.trace_id as string),
    [persistedMessages]
  );
  const savedDocragQuery = useSavedDocragTraceIds(businessViewId, replyTraceIds);
  const docragTraceIds = savedDocragQuery.data ?? EMPTY_TRACE_IDS;

  const createConversation = useCreateConversation();
  const updateConversation = useUpdateConversation();
  const deleteConversation = useDeleteConversation();
  const confirm = useConfirm();
  const compareModelsQuery = useCompareModels();
  const compareModels = compareModelsQuery.data ?? [];

  const [composer, setComposer] = useWorkspaceState("chat.composer", "");
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
  }, [businessViewId, setActiveId]);

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
      // 新しい会話は一覧の先頭（更新日時の新しい順）に入るので、1 ページ目に戻して見せる。
      setConversationOffset(0);
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

  // 会話の削除は取り消せないため、確認ダイアログを通す（buttons.md §5.1）。
  async function removeConversation(conversation: ConversationSummary) {
    const title = conversation.title ?? t("chat.sessions.untitled");
    const ok = await confirm({
      title: t("chat.sessions.deleteConfirm.title"),
      description: t("chat.sessions.deleteConfirm.description", { title }),
      confirmLabel: t("chat.sessions.deleteConfirm.confirm"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    try {
      if (conversation.id === activeId) {
        abortRef.current?.abort();
        setLiveTurn(null);
        setErrorText("");
        setActiveId(null);
      }
      await deleteConversation.mutateAsync(conversation.id);
      toast.success(t("chat.sessions.deleted"));
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.messages.join(" / ") : t("chat.sessions.deleteError")
      );
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

  /** 送信する。`retryContent` を渡すと、入力欄ではなくその質問（失敗した回答の質問）を送り直す。 */
  async function send(retryContent?: string) {
    const content = (retryContent ?? composer).trim();
    if (!content || !activeId || sending || businessViewWithoutKnowledgeBases) return;
    setSending(true);
    setErrorText("");
    if (retryContent === undefined) setComposer("");
    const controller = new AbortController();
    abortRef.current = controller;
    let started = false;
    try {
      await streamChatMessage(
        activeId,
        { content, model_ids: selectedModelIds },
        {
          onStart: ({ user_message, columns }) => {
            started = true;
            // 送った会話は一覧の先頭へ移るので、1 ページ目に戻して選択中の行を見せる（#403）。
            setConversationOffset(0);
            void queryClient.invalidateQueries({ queryKey: ["conversations"] });
            const startedAtMs = Date.now();
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
                docrag: null,
                stages: [],
                startedAtMs,
              })),
            });
          },
          onStage: ({ model_id, stage, outcome }) => {
            setLiveTurn((current) => {
              if (!current) return current;
              return {
                ...current,
                columns: current.columns.map((column) =>
                  column.model_id === model_id
                    ? { ...column, stages: [...column.stages, { stage, outcome }] }
                    : column
                ),
              };
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
          onMetadata: ({ model_id, trace_id, guardrail_warnings, docrag }) =>
            updateColumn(model_id, {
              traceId: trace_id,
              guardrailWarnings: guardrail_warnings,
              docrag: docrag ?? null,
            }),
          onCitations: (modelId, citations) => updateColumn(modelId, { citations }),
          onModelDone: ({ model_id }) => updateColumn(model_id, { status: "done" }),
          onModelError: ({ model_id, message }) =>
            updateColumn(model_id, { status: "error", errorMessage: message }),
          onAllDone: async () => {
            void queryClient.invalidateQueries({ queryKey: ["docrag-answers"] });
            await queryClient.invalidateQueries({ queryKey: ["conversations"] });
            setLiveTurn(null);
          },
        },
        controller.signal
      );
    } catch (error) {
      if (!controller.signal.aborted) {
        setErrorText(error instanceof ApiError ? error.messages.join(" / ") : t("chat.error.send"));
        // 質問を保存する前に失敗したら、入力を戻して送り直せるようにする。
        // 生成中に書き始めた次の質問は上書きしない。
        if (!started && retryContent === undefined) {
          setComposer((current) => (current.trim() ? current : content));
        }
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
        docrag: column.docrag,
        progress: { stages: column.stages, startedAtMs: column.startedAtMs },
      }))
    : [];
  const lastTurnId = turns.length ? turns[turns.length - 1].user.message_id : null;

  return (
    <div className="flex min-h-full flex-col lg:h-full lg:min-h-0">
      <PageHeader wide title={t("chat.title")} subtitle={t("chat.subtitle")} />

      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4">
        {/* 業務ビュー scope */}
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {businessViewLoading ? (
              <TimedLoadingState
                label={t("chat.businessView.loading")}
                operationKey="chat-business-views-load"
                framed={false}
                testId="chat-business-views-loading"
              >
                <Skeleton className="h-[var(--button-height-md)] w-full max-w-md" />
              </TimedLoadingState>
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
              <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2 2xl:grid-cols-3">
                <SelectField
                  id="chat-business-view"
                  label={t("chat.businessView.label")}
                  value={businessViewId ?? ""}
                  options={businessViewOptions}
                  onValueChange={(value) => setBusinessViewId(value || null)}
                  required
                />
                {businessViewWithoutKnowledgeBases && businessViewId ? (
                  <Banner
                    severity="warning"
                    className="lg:col-span-2 2xl:col-span-3"
                    action={
                      canOpenBusinessViews ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            navigate(
                              `${APP_ROUTES.businessViews}?id=${encodeURIComponent(businessViewId)}`
                            )
                          }
                        >
                          {t("chat.businessView.openSettings")}
                        </Button>
                      ) : undefined
                    }
                  >
                    {t("chat.businessView.noKnowledgeBases")}
                  </Banner>
                ) : null}
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
              className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface p-3 shadow-sm lg:min-h-0"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-fg">
                  {t("chat.sessions.title")}
                </span>
                <Button
                  size="sm"
                  className="h-11 sm:h-8"
                  onClick={() => void startNewConversation()}
                  disabled={createConversation.isPending} icon={Plus}>
                  {t("chat.sessions.new")}
                </Button>
              </div>
              {conversationsQuery.isLoading ? (
                <TimedLoadingState
                  label={t("chat.sessions.loading")}
                  operationKey="chat-conversations-load"
                  framed={false}
                  testId="chat-conversations-loading"
                >
                  <ListSkeleton rows={3} rowClassName="h-12" />
                </TimedLoadingState>
              ) : conversationsQuery.isError ? (
                <ErrorState
                  message={t("chat.sessions.error")}
                  onRetry={() => void conversationsQuery.refetch()}
                />
              ) : conversations.length === 0 ? (
                <p className="px-1 text-sm text-fg-muted">{t("chat.sessions.empty")}</p>
              ) : (
                <>
                <ul
                  // lg 未満は 5 / 8 行の高さで中をスクロールし、lg 以上は会話エリアの高さまで伸ばす（#403）。
                  className={cn(
                    "min-h-0 flex-1 space-y-1",
                    INFORMATION_LIST_SCROLL_CLASS,
                    "lg:max-h-none"
                  )}
                  aria-label={t("chat.sessions.title")}
                  data-testid="chat-conversation-list"
                >
                  {conversations.map((conversation) => {
                    const title = conversation.title ?? t("chat.sessions.untitled");
                    return (
                      <li key={conversation.id} className="group">
                        {editingId === conversation.id ? (
                          <div className="rounded-md bg-accent-subtle p-2">
                            <div className="flex items-start gap-1">
                              <TextField
                                ref={titleInputRef}
                                id={`conversation-title-${conversation.id}`}
                                label={t("chat.sessions.renameLabel")}
                                labelHidden
                                className="min-w-0 flex-1"
                                value={titleDraft}
                                maxLength={80}
                                required
                                disabled={updateConversation.isPending}
                                error={titleError || undefined}
                                onValueChange={setTitleDraft}
                                onKeyDown={(event) => {
                                  if (isSubmitEnter(event)) {
                                    event.preventDefault();
                                    void saveRename();
                                  } else if (event.key === "Escape") {
                                    event.preventDefault();
                                    cancelRename();
                                  }
                                }}
                              />
                              <Button
                                type="button"
                                variant="ghost"
                                size="md"
                                iconOnly
                                disabled={updateConversation.isPending}
                                aria-label={t("chat.sessions.renameSave")}
                                onClick={() => void saveRename()} icon={Check}>
                                </Button>
                              <Button
                                type="button"
                                variant="ghost"
                                size="md"
                                iconOnly
                                disabled={updateConversation.isPending}
                                aria-label={t("chat.sessions.renameCancel")}
                                onClick={cancelRename} icon={X}>
                                </Button>
                            </div>
                          </div>
                        ) : (
                          <div
                            className={cn(
                              "grid grid-cols-[minmax(0,1fr)_auto_auto] rounded-md transition-colors",
                              INFORMATION_LIST_ROW_CLASS,
                              conversation.id === activeId
                                ? "bg-accent-subtle text-fg"
                                : "text-fg-muted hover:bg-surface-hover hover:text-fg"
                            )}
                          >
                            <button
                              type="button"
                              onClick={() => selectConversation(conversation.id)}
                              aria-current={conversation.id === activeId}
                              className="flex min-w-0 flex-col gap-0.5 rounded-md px-3 py-2 text-left text-sm focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-focus-ring"
                            >
                              <span className="truncate font-medium" title={title}>
                                {title}
                              </span>
                              <span className="text-xs tabular-nums text-fg-muted">
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
                              iconOnly
                              className={cn(
                                "mr-1 self-center transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100",
                                conversation.id === activeId && "sm:opacity-100"
                              )}
                              aria-label={t("chat.sessions.rename", { title })}
                              onClick={() => startRename(conversation)} icon={Pencil}>
                              </Button>
                            <Button
                              type="button"
                              variant="ghost"
                              size="md"
                              tone="danger"
                              iconOnly
                              className={cn(
                                "mr-1 self-center transition-opacity sm:opacity-0 sm:group-focus-within:opacity-100 sm:group-hover:opacity-100",
                                conversation.id === activeId && "sm:opacity-100"
                              )}
                              disabled={deleteConversation.isPending}
                              aria-label={t("chat.sessions.delete", { title })}
                              onClick={() => void removeConversation(conversation)} icon={Trash2}>
                              </Button>
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
                {conversationsData ? (
                  <ListPagination
                    {...offsetPagination({
                      // 次のページを取得している間は、表示中のページ（前のページ）の範囲を出す。
                      offset: conversationsData.offset,
                      limit: DEFAULT_PAGE_SIZE,
                      total: conversationsData.total,
                      count: conversations.length,
                    })}
                    onPageChange={(next) => setConversationOffset(offsetForPage(next, DEFAULT_PAGE_SIZE))}
                    ariaLabel={t("chat.sessions.pagination")}
                    testId="chat-conversations-pagination"
                  />
                ) : null}
                </>
              )}
            </aside>

            {/* 会話エリア */}
            <section
              aria-label={t("chat.title")}
              className="flex h-[70dvh] min-h-[28rem] min-w-0 flex-col gap-3 overflow-hidden rounded-lg border border-border bg-surface shadow-sm lg:h-auto lg:min-h-0"
            >
            <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
              {!activeId ? (
                <EmptyState
                  title={t("chat.composer.selectConversation")}
                  hint={t("chat.messages.empty")}
                />
              ) : conversationQuery.isLoading ? (
                <TimedLoadingState
                  label={t("chat.messages.loading")}
                  operationKey="chat-conversation-load"
                  framed={false}
                  testId="chat-messages-loading"
                >
                  {/* 質問と回答の吹き出しの寸法を予約する。 */}
                  <Skeleton className="ml-auto h-12 w-2/3" />
                  <Skeleton className="h-32 w-5/6" />
                  <Skeleton className="ml-auto h-12 w-1/2" />
                </TimedLoadingState>
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
                      onRetry={
                        // 最新の質問の失敗（時間切れなど）だけ、同じ質問をもう一度送れる（#375）。
                        turn.user.message_id === lastTurnId &&
                        turn.user.status !== "ERROR" &&
                        !liveTurn &&
                        !sending
                          ? () => void send(turn.user.content)
                          : undefined
                      }
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
                        savedDocrag: Boolean(
                          reply.trace_id && docragTraceIds.has(reply.trace_id)
                        ),
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
                  <span className="text-xs font-medium text-fg-muted">{t("chat.compare.label")}</span>
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
                <TextareaField
                  ref={composerRef}
                  id="chat-composer"
                  label={t("chat.composer.placeholder")}
                  labelHidden
                  value={composer}
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    // IME の変換を確定する Enter では送信しない（#459）。
                    if (isSubmitEnter(event) && !event.shiftKey) {
                      event.preventDefault();
                      void send();
                    }
                  }}
                  rows={2}
                  placeholder={
                    activeId ? t("chat.composer.placeholder") : t("chat.composer.selectConversation")
                  }
                  // 生成中も入力できる（次の質問を書ける）。生成中の Enter は send が無視し、停止しない（#413）。
                  // 生成中に disabled にすると、Enter で送った直後にフォーカスが body へ外れる。
                  disabled={!activeId}
                  // ラベルは読み上げだけ（sr-only）なので、欄の上に余白を空けず送信ボタンと下端をそろえる。
                  className="min-w-0 flex-1 space-y-0"
                  textareaClassName="min-h-11"
                />
                {/* 送信と停止は同じボタン。生成中は同じ位置で「停止」になる（buttons.md §3.1、#413）。 */}
                <RunStopButton
                  running={sending}
                  onRun={() => void send()}
                  onStop={stop}
                  runLabel={t("chat.composer.send")}
                  stopLabel={t("chat.composer.stop")}
                  runIcon={SendHorizontal}
                  runDisabled={
                    !activeId || composer.trim().length === 0 || businessViewWithoutKnowledgeBases
                  }
                  size="md"
                  className="w-full shrink-0 sm:w-auto"
                  testId="chat-run-stop"
                />
              </div>
              {errorText ? (
                <p className="text-sm text-danger-fg" role="alert">
                  {errorText}
                </p>
              ) : null}
            </div>
            </section>
          </div>
        )}
      </PageBody>
    </div>
  );
}
