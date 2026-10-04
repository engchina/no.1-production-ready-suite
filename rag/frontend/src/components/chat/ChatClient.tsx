import {
  ChatLayout,
  ChatProgress,
  ChatSkeleton,
  Disclosure,
  PageBody,
  PageHeader,
  Button,
  Banner,
  Card,
  CardContent,
  FieldActionRow,
  TextareaField,
  TextField,
  InfoTip,
  ToggleChip,
  TimedLoadingState,
  ListSkeleton,
  DEFAULT_PAGE_SIZE,
  INFORMATION_LIST_ROW_CLASS,
  offsetForPage,
  offsetPagination,
  toast,
  useConfirm,
  RunStopButton,
  ChatUserMessage,
  createOptimisticChatMessage,
  useChatProgressTracker,
  withOptimisticChatStatus,
  type ChatUserMessageStatus,
  type OptimisticChatMessage,
  type ChatProgressStep,
  useChatHistoryPanel,
} from "@engchina/production-ready-ui";
import {
  Check,
  Pencil,
  RotateCcw,
  Search,
  SendHorizontal,
  Square,
  Trash2,
  X,
} from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import { ListPagination } from "@/components/ListPagination";
import { SearchAnswerProfileSelect, SearchAnswerProfileSelectSkeleton } from "@/components/search-answer-profiles/SearchAnswerProfileSelect";
import { CitationCard } from "@/components/search/CitationCard";
import { SavedAnswerRecord } from "@/components/search/SavedAnswerRecord";
import { AnswerDetailsPanel } from "@/components/search/AnswerDetailsPanel";
import { AnswerText } from "@/components/search/AnswerText";
import { useAuth } from "@/components/security/AuthProvider";
import { ApiErrorState, EmptyState, ErrorState } from "@/components/StateViews";
import { isSubmitEnter } from "@/lib/keyboard";
import type {
  ApprovedFaqSuggestionData,
  ClarificationAnswer,
  ClarificationSuggestionData,
  ChatMessage,
  ConversationSummary,
  RetrievedChunk,
} from "@/lib/api";
import { api, ApiError } from "@/lib/api";
import { ApprovedFaqSuggestions } from "@/components/search/ApprovedFaqSuggestions";
import { ClarificationChoice } from "./ClarificationChoice";
import { chatSubmitProgressSteps } from "@/lib/chat-progress";
import { streamChatMessage, type ChatColumn } from "@/lib/chat-stream";
import { answerModelHelpKey, answerModelLabel } from "@/lib/answer-models";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { isNullableString, useWorkspaceState } from "@/lib/workspace-state";
import {
  useSearchAnswerProfiles,
  useCompareModels,
  useConversation,
  useConversations,
  useCreateConversation,
  useSavedAnswerTraceIds,
  useUpdateConversation,
  useDeleteConversation,
  queryKeys,
} from "@/lib/queries";
import { useQueryClient } from "@tanstack/react-query";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

const COMPARE_MAX = 3;
/**
 * 回答の作成中に、配信（event・heartbeat）がこの時間届かなければ途絶えたとみなし、保存済みの会話を
 * 取り直す（#1160）。backend は event の無い間も 10 秒ごとに heartbeat を送るので、その数回分。
 */
const CHAT_STREAM_STALE_AFTER_MS = 30_000;
/** `all_done` の前に配信が終わったとき、保存済みの回答を待つ時間。過ぎたら理由と「再送信」を出す。 */
const CHAT_STREAM_LOST_GIVE_UP_MS = 10_000;
/** 配信が途絶えたまま、この時間が過ぎたら接続を閉じて理由と「再送信」を出す。 */
const CHAT_STREAM_STALL_GIVE_UP_MS = 120_000;

/** ref に今の時刻を入れる（送信・配信の event の処理から呼ぶ。描画中には呼ばない）。 */
function stampNow(ref: { current: number | null }) {
  ref.current = Date.now();
}
const EMPTY_TRACE_IDS: ReadonlySet<string> = new Set();

/** 会話一覧のページ（検索・回答プロファイルごと。#403）。別の検索・回答プロファイルに移ったら 1 ページ目から。 */
interface ConversationsPage {
  searchAnswerProfileId: string | null;
  offset: number;
}

function isConversationsPage(value: unknown): value is ConversationsPage {
  const page = value as ConversationsPage;
  return (
    typeof page === "object" &&
    page !== null &&
    isNullableString(page.searchAnswerProfileId) &&
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
  answerDiagnostics: unknown;
  /** 処理の段階（3 製品共通の ChatProgressStep。#1146）。最初の `progress` が届くまでは空。 */
  progressSteps: ChatProgressStep[];
  startedAtMs: number;
}

/** 送信の条件（再送信で同じ条件のまま送り直す）。 */
interface DeliverRequest {
  content: string;
  approvedFaqId?: string;
  clarification?: ClarificationAnswer;
}

/**
 * 送信中・生成中・失敗・停止の 1 往復（#907）。
 *
 * 送信した瞬間に仮の質問（`pending`）で作り、会話の作成・サーバーの `start` を待たずに会話の欄の末尾へ出す。
 * `start` で保存済みの質問（`user`）が届いたら置き換え、`all_done` で取り直した会話に引き継いで外す
 * （同じ質問を二重に出さない）。失敗・停止のときは外さずに残す（送った質問を失わない）。
 */
interface LiveTurn {
  pending: OptimisticChatMessage;
  /** サーバーが保存した質問。`start` が届くまでは null。 */
  user: ChatMessage | null;
  columns: LiveColumn[];
  /** 送信できなかった理由（`pending.status` が `failed` のとき）。 */
  failureMessage: string | null;
  request: DeliverRequest;
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
  searchAnswerProfileId,
  messageId,
  streaming,
  errorMessage,
  guardrailWarnings,
  answerDiagnostics = null,
  savedAnswer = false,
  progress = null,
  onRetry,
  className,
}: {
  label: string | null;
  answer: string;
  citations: RetrievedChunk[];
  traceId: string | null;
  searchAnswerProfileId: string;
  messageId: string | null;
  streaming: boolean;
  errorMessage: string | null;
  guardrailWarnings: string[];
  answerDiagnostics?: unknown;
  /** 保存された回答がある(trace_id から根拠と実行記録を開ける)。 */
  savedAnswer?: boolean;
  /**
   * 処理の段階（#1146）。生成中は今の段階と経過時間、完了後は回答の上に「処理の経過」の 1 行を出す。
   * 段階がまだ届いていない（送信の応答待ち）ときは `steps` を空にし、「質問を送信しています」を出す。
   */
  progress?: { steps: ChatProgressStep[]; startedAtMs: number; reconnecting?: boolean } | null;
  /** 失敗した回答をもう一度送信する（最新の質問だけ）。 */
  onRetry?: () => void;
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
      {label ? (
        // 1 列（既定のモデル）でも、どのモデルの回答かを出す（#649）。
        <h3
          className="truncate border-b border-border pb-2 text-sm font-semibold text-fg"
          title={label}
        >
          {t("chat.column.model", { name: label })}
        </h3>
      ) : null}
      {progress ? (
        // 処理の段階（3 製品共通の ChatProgress。#1145 / #1146）。回答の本文は生成と検査が終わってから
        // まとめて届くので、それまでは今の段階と経過時間を出す。失敗の原因は段階ではなく下の Banner に出す。
        <ChatProgress
          steps={progress.steps.length > 0 ? progress.steps : chatSubmitProgressSteps(progress.startedAtMs)}
          reconnecting={progress.reconnecting}
          testId="chat-answer-progress"
        />
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
      ) : waitingForAnswer ? null : (
        <AnswerText
          text={answer}
          streaming={streaming}
          citations={citations}
          live
          cursor={
            streaming ? (
              <span className="ml-0.5 inline-block animate-pulse motion-reduce:animate-none">▍</span>
            ) : null
          }
        />
      )}
      {guardrailWarnings.length > 0 ? (
        <Banner severity="warning">
          <span className="font-medium">{t("chat.guardrail")}: </span>
          {guardrailWarnings.join(" / ")}
        </Banner>
      ) : null}
      {!streaming && !errorMessage && answerDiagnostics ? <AnswerDetailsPanel diagnostics={answerDiagnostics} traceId={traceId} /> : null}
      {!streaming && !errorMessage && !answerDiagnostics && savedAnswer && traceId ? (
        <Disclosure
          variant="plain"
          summary={t("chat.answerDetails.open")}
          className="border-t border-border px-2 pt-1"
        >
          <SavedAnswerRecord traceId={traceId} searchAnswerProfileId={searchAnswerProfileId} showAnswer={false} />
        </Disclosure>
      ) : null}
      {!streaming && !errorMessage ? (
        <FeedbackControls
          traceId={traceId}
          searchAnswerProfileId={searchAnswerProfileId}
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
                searchAnswerProfileId={searchAnswerProfileId}
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
  userStatus = "sent",
  columns,
  searchAnswerProfileId,
  onRetry,
  onAskUnscoped,
  footer,
  testId,
}: {
  user: Pick<ChatMessage, "content" | "guardrail_warnings">;
  /** 質問の状態（送信中・失敗・停止。#907）。保存済みの質問は `sent`。 */
  userStatus?: ChatUserMessageStatus;
  /** 回答の後に出す送信の失敗・停止の表示（#907）。 */
  footer?: ReactNode;
  testId?: string;
  searchAnswerProfileId: string;
  /** 失敗した回答があるときに同じ質問をもう一度送る（最新のターンだけ渡す）。 */
  onRetry?: () => void;
  /** 確認で範囲を絞った回答のとき、範囲を指定せずに同じ質問を送る（最新のターンだけ渡す。#721）。 */
  onAskUnscoped?: () => void;
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
    answerDiagnostics?: unknown;
    savedAnswer?: boolean;
    progress?: { steps: ChatProgressStep[]; startedAtMs: number; reconnecting?: boolean } | null;
  }[];
}) {
  const compare = columns.length > 1;
  // 範囲を絞った回答は末尾に「（対象: …）」が付く（#717）。
  const scoped =
    onAskUnscoped !== undefined &&
    columns.some(
      (column) => !column.streaming && !column.errorMessage && column.answer.includes("（対象:")
    );
  return (
    <div className="space-y-2" data-testid={testId}>
      <ChatUserMessage status={userStatus} failedLabel={t("chat.send.failed")}>
        {user.content}
      </ChatUserMessage>
      {user.guardrail_warnings.length > 0 ? (
        <div className="ml-auto max-w-[85%]">
          <Banner severity="warning">
            <span className="font-medium">{t("chat.guardrail")}: </span>
            {user.guardrail_warnings.join(" / ")}
          </Banner>
        </div>
      ) : null}
      {columns.length > 0 ? (
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
              searchAnswerProfileId={searchAnswerProfileId}
              messageId={column.messageId}
              streaming={column.streaming}
              errorMessage={column.errorMessage}
              guardrailWarnings={column.guardrailWarnings}
              answerDiagnostics={column.answerDiagnostics}
              savedAnswer={column.savedAnswer}
              progress={column.progress}
              onRetry={column.errorMessage ? onRetry : undefined}
              className={
                compare && columns.length % 2 === 1 && index === columns.length - 1
                  ? "col-span-full"
                  : undefined
              }
            />
          ))}
        </div>
      ) : null}
      {footer}
      {scoped ? (
        <div className="flex justify-end">
          <Button type="button" variant="secondary" size="sm" icon={Search} onClick={onAskUnscoped}>
            {t("chat.clarify.askUnscoped")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

export function ChatClient() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();

  const searchAnswerProfilesQuery = useSearchAnswerProfiles({ status: "ACTIVE", limit: 50, offset: 0 });
  const searchAnswerProfiles = searchAnswerProfilesQuery.data?.items ?? [];
  // 選択中の検索・回答プロファイル・会話・入力中の下書きは、ページを行き来しても再読込しても残す
  // （workspace-state.md）。URL の deep-link があればそちらを優先する。
  const urlSearchAnswerProfileId = searchParams.get("search_answer_profile_id");
  const urlConversationId = searchParams.get("conversation_id");
  const [searchAnswerProfileId, setSearchAnswerProfileId] = useWorkspaceState<string | null>(
    "chat.searchAnswerProfileId",
    null,
    isNullableString,
    urlSearchAnswerProfileId ?? undefined
  );
  // 復元した検索・回答プロファイルがアーカイブ・削除済みなら選択を外す（別の検索・回答プロファイルへ置き換えない）。
  const searchAnswerProfileMissing =
    Boolean(searchAnswerProfileId) &&
    Boolean(searchAnswerProfilesQuery.data) &&
    !searchAnswerProfilesQuery.data?.has_next &&
    !searchAnswerProfiles.some((view) => view.id === searchAnswerProfileId);
  useEffect(() => {
    if (searchAnswerProfileMissing) setSearchAnswerProfileId(null);
  }, [searchAnswerProfileMissing, setSearchAnswerProfileId]);
  // 参照 KB が 0 件の検索・回答プロファイルではチャットしない（利用者の全 KB を検索しない。#304）。
  // backend も送信時に 409 で理由を返すが、送信する前にこの場で理由を示す。
  const canOpenSearchAnswerProfiles = useAuth().hasPermission(MENU_PERMISSIONS.searchAnswerProfiles);
  const selectedSearchAnswerProfile = searchAnswerProfiles.find((view) => view.id === searchAnswerProfileId);
  const searchAnswerProfileWithoutKnowledgeBases = selectedSearchAnswerProfile?.knowledge_base_count === 0;
  // 会話一覧はサーバー側でページングする（50 件で打ち切らない。#403）。
  // ページは作業状態として残す（workspace-state.md）。別の検索・回答プロファイルに移ったら 1 ページ目から。
  const [conversationsPage, setConversationsPage] = useWorkspaceState<ConversationsPage>(
    "chat.conversationsPage",
    { searchAnswerProfileId, offset: 0 },
    isConversationsPage
  );
  const conversationOffset =
    conversationsPage.searchAnswerProfileId === searchAnswerProfileId ? conversationsPage.offset : 0;
  const setConversationOffset = (offset: number) =>
    setConversationsPage({ searchAnswerProfileId, offset });
  const conversationsQuery = useConversations({
    search_answer_profile_id: searchAnswerProfileId ?? undefined,
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
    urlConversationId ?? (urlSearchAnswerProfileId ? null : undefined)
  );
  const conversationQuery = useConversation(activeId);
  // 画面の前提（検索・回答プロファイルの一覧・開いている会話の内容）が揃うまでは、会話の欄に空の状態を出さず
  // 会話の形の Skeleton で覆い、入力欄・送信・新しい会話・履歴の開閉を無効にする（messaging.md §11.7、#1153）。
  // 会話の内容を読めなかったときも送信しない（内容の分からない会話に続けて送らない）。
  const conversationLoading = Boolean(activeId) && conversationQuery.isLoading;
  const conversationFailed = Boolean(activeId) && conversationQuery.isError && !conversationQuery.data;
  const prerequisitesLoading = searchAnswerProfilesQuery.isLoading || conversationLoading;
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
  const savedAnswerQuery = useSavedAnswerTraceIds(searchAnswerProfileId, replyTraceIds);
  const answerTraceIds = savedAnswerQuery.data ?? EMPTY_TRACE_IDS;

  const createConversation = useCreateConversation();
  const updateConversation = useUpdateConversation();
  const deleteConversation = useDeleteConversation();
  const confirm = useConfirm();
  const compareModelsQuery = useCompareModels();
  const compareModels = compareModelsQuery.data ?? [];
  const modelLabels = new Map(compareModels.map((model) => [model.model_id, model.display_name]));

  const [composer, setComposer] = useWorkspaceState("chat.composer", "");
  // 回答の前に選ぶ類似の承認済み FAQ（選ぶまで送らない。#684）。選ぶ前に画面を離れても、戻ったとき・
  // 再読込で同じ質問と候補を出す（作業状態。#702）。検索・回答プロファイルを変えたら消す。
  const [faqChoice, setFaqChoice] = useWorkspaceState<FaqChoice | null>(
    "chat.faqChoice",
    null,
    isFaqChoice
  );
  // 回答の前に答える確認の質問（検索・回答プロファイルのルール。#717）。類似問の提示の後に出し、作業状態に残す。
  const [clarifyChoice, setClarifyChoice] = useWorkspaceState<ClarifyChoice | null>(
    "chat.clarifyChoice",
    null,
    isClarifyChoice
  );
  const pendingChoice = faqChoice !== null || clarifyChoice !== null;
  // 会話の履歴は既定で閉じる（ChatGPT・Claude・Gemini・Copilot と同じ。多くの利用者は履歴を使わないので、
  // チャットに面積を渡す。#664）。lg 以上のインラインのパネルの開閉は作業状態に残す。
  // lg 未満のモーダルの side sheet は残さない（戻ったとき・再読込で画面を塞がない。workspace-state.md）。
  // 開閉の判定・シートの開閉は共通の useChatHistoryPanel（3 製品共通。#1161）。
  const [historyPanelOpen, setHistoryPanelOpen] = useWorkspaceState("chat.historyOpen", false);
  const history = useChatHistoryPanel({
    inlineOpen: historyPanelOpen,
    onInlineOpenChange: setHistoryPanelOpen,
  });
  const [selectedModelIds, setSelectedModelIds] = useState<string[]>([]);
  const [liveTurn, setLiveTurn] = useState<LiveTurn | null>(null);
  // この画面で送って保存された回答の処理の段階（message_id → 段階。#1146）。段階は保存しないので、
  // 会話を取り直した後も、この画面を開いている間は回答の上に「処理の経過」を残す。
  const [finishedProgress, setFinishedProgress] = useState<Record<string, ChatProgressStep[]>>({});
  const [sending, setSending] = useState(false);
  const [errorText, setErrorText] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [titleDraft, setTitleDraft] = useState("");
  const [titleError, setTitleError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const previousSearchAnswerProfileIdRef = useRef(searchAnswerProfileId);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);

  // 検索・回答プロファイルを切り替えたら会話選択と進行中ストリームをリセットする。
  useEffect(() => {
    if (previousSearchAnswerProfileIdRef.current === searchAnswerProfileId) return;
    previousSearchAnswerProfileIdRef.current = searchAnswerProfileId;
    // 送信・生成を打ち切り、送信中の状態も解く（cancelSending と同じ。照会の応答を待たない）。
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    setActiveId(null);
    setLiveTurn(null);
    setFaqChoice(null);
    setClarifyChoice(null);
    setErrorText("");
    setEditingId(null);
    setTitleError("");
  }, [searchAnswerProfileId, setActiveId, setFaqChoice, setClarifyChoice]);

  // メッセージが増えたら末尾までスクロールする。
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [persistedMessages.length, liveTurn, faqChoice, clarifyChoice]);

  useEffect(() => {
    const targetId = location.hash.slice(1);
    if (!targetId || !persistedMessages.length) return;
    const animationFrame = window.requestAnimationFrame(() => {
      const container = scrollRef.current;
      const target = document.getElementById(targetId);
      if (!container || !target || !container.contains(target)) return;
      const offset = target.getBoundingClientRect().top - container.getBoundingClientRect().top;
      container.scrollTo({ top: container.scrollTop + offset - (container.clientHeight - target.clientHeight) / 2 });
    });
    return () => window.cancelAnimationFrame(animationFrame);
  }, [location.hash, persistedMessages.length]);

  useEffect(() => () => abortRef.current?.abort(), []);

  // 回答の配信（SSE）の状態を追う（3 製品共通の useChatProgressTracker。#1160）。質問が保存された（start）後に
  // 配信が途絶えた・`all_done` の前に終わったときは、保存済みの会話を取り直して回答に置き換える。
  const liveTurnRef = useRef<LiveTurn | null>(liveTurn);
  const activeIdRef = useRef(activeId);
  useLayoutEffect(() => {
    liveTurnRef.current = liveTurn;
    activeIdRef.current = activeId;
  });
  /** `all_done` の前に配信が終わった時刻（終わっていなければ null）。 */
  const streamLostAtRef = useRef<number | null>(null);
  /** 最後に配信のバイト（event・heartbeat）が届いた時刻。 */
  const streamActivityAtRef = useRef(0);
  const streamTracker = useChatProgressTracker({
    key: liveTurn?.user?.message_id ?? null,
    steps: liveTurn?.columns[0]?.progressSteps ?? [],
    active: sending && liveTurn?.user != null && liveTurn.pending.status === "sending",
    refresh: recoverStreamedAnswer,
    staleAfterMs: CHAT_STREAM_STALE_AFTER_MS,
  });

  /** 配信を閉じて送信中の状態を解く（取り直しで回答がそろった・あきらめたとき）。 */
  function finishStream() {
    const controller = abortRef.current;
    abortRef.current = null;
    controller?.abort();
    streamLostAtRef.current = null;
    setSending(false);
    void queryClient.invalidateQueries({ queryKey: ["answer-records"] });
    void queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  /**
   * 配信が途絶えた・終わったときに、保存済みの会話から回答を取り直す（#1160）。回答がそろっていれば、回答の
   * 作成中の表示を保存済みの回答に置き換える。そろわないまま上限を過ぎたら、理由と「再送信」を出す。
   */
  async function recoverStreamedAnswer() {
    const turn = liveTurnRef.current;
    const conversationId = activeIdRef.current;
    const userMessageId = turn?.user?.message_id;
    if (!turn || !conversationId || !userMessageId) return;
    const detail = await queryClient.fetchQuery({
      queryKey: queryKeys.conversation(conversationId),
      queryFn: () => api.getConversation(conversationId),
      staleTime: 0,
    });
    // 取り直している間に別の送信・停止に移っていたら何もしない。
    if (liveTurnRef.current?.user?.message_id !== userMessageId || abortRef.current === null) return;
    const replies = detail.messages.filter(
      (message) => message.role === "ASSISTANT" && message.reply_to_message_id === userMessageId
    );
    if (replies.length >= Math.max(1, turn.columns.length)) {
      finishStream();
      setLiveTurn(null);
      return;
    }
    const lostAt = streamLostAtRef.current;
    const waited = Date.now() - (lostAt ?? streamActivityAtRef.current);
    if (waited < (lostAt !== null ? CHAT_STREAM_LOST_GIVE_UP_MS : CHAT_STREAM_STALL_GIVE_UP_MS)) return;
    finishStream();
    setLiveTurn((current) =>
      current
        ? {
            ...current,
            pending: withOptimisticChatStatus(current.pending, "failed"),
            columns: current.columns.filter((column) => column.status !== "streaming"),
            failureMessage: t("chat.stream.lost"),
          }
        : current
    );
  }

  useEffect(() => {
    if (editingId) titleInputRef.current?.focus();
  }, [editingId]);

  useEffect(() => {
    if (titleError && !updateConversation.isPending) titleInputRef.current?.focus();
  }, [titleError, updateConversation.isPending]);

  const liveUserMessageId = liveTurn?.user?.message_id;
  const turns = useMemo(
    () =>
      buildTurns(persistedMessages).filter(
        (turn) => turn.user.message_id !== liveUserMessageId
      ),
    [persistedMessages, liveUserMessageId]
  );

  function selectConversation(id: string) {
    // lg 未満のシートは、会話を選んだら閉じる（開閉ボタンへフォーカスを戻す）。
    history.closeSheet();
    if (id === activeId) return;
    cancelSending();
    setLiveTurn(null);
    setErrorText("");
    setActiveId(id);
  }

  function focusComposer() {
    requestAnimationFrame(() => composerRef.current?.focus());
  }

  async function startNewConversation() {
    if (!searchAnswerProfileId) return;
    setErrorText("");
    // 送れなかった・止めた質問は、新しい会話に持ち越さない（#907）。
    if (liveTurn && liveTurn.pending.status !== "sending") setLiveTurn(null);
    const emptyConversation = conversations.find(
      (conversation) => conversation.status === "ACTIVE" && conversation.message_count === 0
    );
    if (emptyConversation) {
      selectConversation(emptyConversation.id);
      focusComposer();
      return;
    }
    try {
      const created = await createConversation.mutateAsync({ search_answer_profile_id: searchAnswerProfileId });
      // 新しい会話は一覧の先頭（更新日時の新しい順）に入るので、1 ページ目に戻して見せる。
      setConversationOffset(0);
      // 前の会話への送信・生成は、別の会話を選んだときと同じく止める（新しい会話を送信中のままにしない）。
      cancelSending();
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
        cancelSending();
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

  /** 回答の場所（作成中の表示）。`start` で列の構成が届くまでは、選んだモデル（無ければ既定のモデル）で仮に作る。 */
  function placeholderColumns(startedAtMs: number): LiveColumn[] {
    const modelIds = selectedModelIds.length > 0 ? selectedModelIds : [""];
    return modelIds.map((modelId) => ({
      model_id: modelId,
      label: modelId ? (modelLabels.get(modelId) ?? modelId) : "",
      answer: "",
      citations: [],
      status: "streaming",
      traceId: null,
      errorMessage: null,
      guardrailWarnings: [],
      answerDiagnostics: null,
      progressSteps: [],
      startedAtMs,
    }));
  }

  /** 送った質問を、サーバーの応答を待たずに会話の欄の末尾へ出す 1 往復（#907）。 */
  function newLiveTurn(request: DeliverRequest): LiveTurn {
    const pending = createOptimisticChatMessage(request.content);
    return {
      pending,
      user: null,
      columns: placeholderColumns(pending.sentAtMs),
      failureMessage: null,
      request,
    };
  }

  /**
   * 送信・生成を打ち切り、送信中の状態をすぐ解く（別の会話・検索・回答プロファイルに移るとき）。
   * 類似問・確認の照会は中止できず、応答を待つ間は後始末（`releaseController`）に届かないため、
   * ここで `sending` を戻す（戻さないと移った先でも「停止」のままになり送信できない）。
   */
  function cancelSending() {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
  }

  /** 終わった・止めた送信の後始末（後から始めた送信の状態は変えない）。 */
  function releaseController(controller: AbortController) {
    if (abortRef.current !== controller) return;
    abortRef.current = null;
    setSending(false);
  }

  /**
   * 送信する。`retryContent` を渡すと、入力欄ではなくその質問（失敗した回答の質問）を送り直す。
   * 送った質問は、類似問・確認の照会や会話の作成を待たずにすぐ会話の欄へ出し、入力欄を空にする（#907）。
   * 検索・回答プロファイルの類似問の提示がオンで近い承認済み FAQ があれば、回答を作る前に最大 3 件と「どれでもない」を
   * 出し、どれかを選ぶまで送らない（#684）。照会に失敗したときは類似問を使わずに送る。
   */
  async function send(retryContent?: string) {
    const content = (retryContent ?? composer).trim();
    if (
      !content ||
      !searchAnswerProfileId ||
      sending ||
      pendingChoice ||
      searchAnswerProfileWithoutKnowledgeBases ||
      prerequisitesLoading ||
      conversationFailed
    ) {
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setSending(true);
    setErrorText("");
    if (retryContent === undefined) setComposer("");
    setLiveTurn(newLiveTurn({ content }));
    let suggestions: ApprovedFaqSuggestionData[] = [];
    try {
      suggestions = (await api.suggestApprovedFaq(searchAnswerProfileId, content, "chat")).suggestions ?? [];
    } catch {
      // 類似問は補助。照会できなくても回答は作る。
    }
    if (controller.signal.aborted) return;
    if (suggestions.length > 0) {
      // 選ぶまで送らない。質問は類似問の選択と一緒に同じ位置へ出す。
      setLiveTurn(null);
      setFaqChoice({ content, retryContent, suggestions });
      releaseController(controller);
      return;
    }
    await clarifyOrDeliver(content, controller);
  }

  /** 類似問の選択を確定して送る（`faqId` が無ければ「どれでもない」）。 */
  function chooseFaq(faqId?: string) {
    if (!faqChoice) return;
    const { content, retryContent } = faqChoice;
    setFaqChoice(null);
    setSending(true);
    // 類似問を選ぶと資料を検索しないので、確認の質問は「どれでもない」のときだけ出す（#717）。
    if (faqId) {
      void deliver({ content, approvedFaqId: faqId });
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    setLiveTurn(newLiveTurn({ content }));
    void clarifyOrDeliver(content, controller, retryContent);
  }

  /**
   * 質問が検索・回答プロファイルのルールの確認に当たれば、回答の前に確認の質問を出す（#717）。1 つの質問で聞き返すのは
   * 1 回だけ。照会に失敗したときは確認を使わずに送る。
   */
  async function clarifyOrDeliver(content: string, controller: AbortController, retryContent?: string) {
    if (!searchAnswerProfileId) return;
    let suggestion: ClarificationSuggestionData | null = null;
    try {
      suggestion = (await api.suggestClarification(searchAnswerProfileId, content)).suggestion ?? null;
    } catch {
      // 確認は補助。照会できなくても回答は作る。
    }
    if (controller.signal.aborted) return;
    if (suggestion) {
      setLiveTurn(null);
      setClarifyChoice({ content, retryContent, suggestion });
      releaseController(controller);
      return;
    }
    await deliver({ content }, controller);
  }

  /** 確認の答えを確定して送る（`answer` が無ければ「選ばずに回答する」）。 */
  function chooseClarification(answer?: ClarificationAnswer) {
    if (!clarifyChoice) return;
    const { content } = clarifyChoice;
    setClarifyChoice(null);
    setSending(true);
    void deliver({ content, clarification: answer });
  }

  /** 範囲を指定せずに同じ質問を送る（類似問・確認の質問は出さない。#721）。 */
  function askUnscoped(content: string) {
    setSending(true);
    setErrorText("");
    void deliver({ content });
  }

  /** 送信できなかった質問を、同じ条件でもう一度送る（類似問・確認は聞き直さない。#907）。 */
  function resend() {
    if (!liveTurn || sending || pendingChoice) return;
    setSending(true);
    setErrorText("");
    // 同じ質問（同じ localId）を送信中に戻す。経過時間は 0 から数え直す。
    const pending = withOptimisticChatStatus(liveTurn.pending, "sending");
    setLiveTurn({
      ...liveTurn,
      pending,
      user: null,
      columns: placeholderColumns(pending.sentAtMs),
      failureMessage: null,
    });
    void deliver(liveTurn.request);
  }

  async function deliver(request: DeliverRequest, existingController?: AbortController) {
    if (!searchAnswerProfileId) return;
    const { content, approvedFaqId, clarification } = request;
    const controller = existingController ?? new AbortController();
    abortRef.current = controller;
    // 送信の直後に出した質問はそのまま使う。類似問・確認の選択の後は、ここで会話の欄に出す（#907）。
    setLiveTurn((current) =>
      current &&
      current.user === null &&
      current.pending.status === "sending" &&
      current.pending.content === content
        ? { ...current, request }
        : newLiveTurn(request)
    );
    let started = false;
    // 配信が途中で終わり、保存済みの会話から回答を取り直している（後始末は取り直しが行う。#1160）。
    let recovering = false;
    streamLostAtRef.current = null;
    stampNow(streamActivityAtRef);
    // モデルごとの最新の処理の段階（完了した回答の上に残すため。#1146）。
    const latestProgress = new Map<string, ChatProgressStep[]>();
    try {
      let conversationId = activeId;
      if (!conversationId) {
        // 会話を選んでいなければ、最初の送信で会話を作る（会話の履歴を開かずに始められる。#664）。
        // 質問は作成を待たずに出している（#907）。
        const created = await createConversation.mutateAsync({ search_answer_profile_id: searchAnswerProfileId });
        conversationId = created.id;
        // 作った会話の内容（空）を先に入れ、読み込み中の表示で送信中の質問を隠さない。
        queryClient.setQueryData(queryKeys.conversation(created.id), created);
        setConversationOffset(0);
        setActiveId(created.id);
        if (controller.signal.aborted) return;
      }
      const outcome = await streamChatMessage(
        conversationId,
        {
          content,
          model_ids: selectedModelIds,
          ...(approvedFaqId ? { approved_faq_id: approvedFaqId } : {}),
          ...(clarification ? { clarification } : {}),
        },
        {
          onStart: ({ user_message, columns }) => {
            started = true;
            // 送った会話は一覧の先頭へ移るので、1 ページ目に戻して選択中の行を見せる（#403）。
            setConversationOffset(0);
            void queryClient.invalidateQueries({ queryKey: ["conversations"] });
            // 仮の質問を保存済みの質問に置き換える（取り直した会話の同じ質問は turns から除く）。
            // 経過時間は送信した時刻から数え続ける。
            setLiveTurn((current) =>
              current
                ? {
                    ...current,
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
                      answerDiagnostics: null,
                      progressSteps: [],
                      startedAtMs: current.pending.sentAtMs,
                    })),
                  }
                : current
            );
          },
          onProgress: (modelId, steps) => {
            latestProgress.set(modelId, steps);
            updateColumn(modelId, { progressSteps: steps });
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
          onMetadata: ({ model_id, trace_id, guardrail_warnings, answer_diagnostics }) =>
            updateColumn(model_id, {
              traceId: trace_id,
              guardrailWarnings: guardrail_warnings,
              answerDiagnostics: answer_diagnostics ?? null,
            }),
          onCitations: (modelId, citations) => updateColumn(modelId, { citations }),
          onModelDone: ({ model_id, message_id }) => {
            updateColumn(model_id, { status: "done" });
            // 取り直した会話（保存した回答）でも、この画面の間は処理の経過を回答の上に残す（#1146）。
            const steps = latestProgress.get(model_id);
            if (message_id && steps?.length) {
              setFinishedProgress((current) => ({ ...current, [message_id]: steps }));
            }
          },
          onModelError: ({ model_id, message }) =>
            updateColumn(model_id, { status: "error", errorMessage: message }),
          onAllDone: async () => {
            void queryClient.invalidateQueries({ queryKey: ["answer-records"] });
            // 取り直した会話に引き継いでから外す（質問と回答を一瞬消したり、二重に出したりしない）。
            await queryClient.invalidateQueries({ queryKey: ["conversations"] });
            setLiveTurn(null);
          },
          onActivity: () => {
            stampNow(streamActivityAtRef);
            streamTracker.touch();
          },
        },
        controller.signal
      );
      if (!outcome.completed && !controller.signal.aborted) {
        // `all_done` の前に配信が終わった（接続が切れた）。質問の保存の前なら送信の失敗として扱う。
        if (!started) throw new Error("chat stream ended before start");
        recovering = true;
        stampNow(streamLostAtRef);
        streamTracker.refreshNow();
      }
    } catch (error) {
      if (!controller.signal.aborted && started) {
        // 質問の保存の後に受信が切れた。保存済みの会話から回答を取り直す（無ければ理由と「再送信」。#1160）。
        recovering = true;
        stampNow(streamLostAtRef);
        streamTracker.refreshNow();
      } else if (!controller.signal.aborted) {
        // 送った質問は会話の欄に残し、理由と「再送信」を出す（入力を失わない。#907）。
        const message =
          error instanceof ApiError ? error.messages.join(" / ") : t("chat.send.failedHint");
        setLiveTurn((current) =>
          current
            ? {
                ...current,
                pending: withOptimisticChatStatus(current.pending, "failed"),
                // 質問の保存の前の失敗なので、回答の場所（作成中の表示）は外す。
                columns: [],
                failureMessage: message,
              }
            : current
        );
      }
    } finally {
      if (!recovering) releaseController(controller);
    }
  }

  /** 生成を止める。送った質問は会話の欄に残す（#907）。 */
  function stop() {
    const saved = liveTurn?.user != null;
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    setLiveTurn((current) =>
      current
        ? {
            ...current,
            pending: withOptimisticChatStatus(current.pending, "stopped"),
            columns: [],
          }
        : current
    );
    // 質問を保存した後に止めたら、保存済みの内容を取り直す。
    if (saved) void queryClient.invalidateQueries({ queryKey: ["conversations"] });
  }

  const searchAnswerProfileLoading = searchAnswerProfilesQuery.isLoading;
  // 一覧を読めなかったときは「0 件」と同じに扱わない（空の状態を出さず、失敗と再試行を出す。#1153）。
  const searchAnswerProfilesFailed = searchAnswerProfilesQuery.isError && !searchAnswerProfilesQuery.data;
  const noSearchAnswerProfiles =
    !searchAnswerProfileLoading && !searchAnswerProfilesFailed && searchAnswerProfiles.length === 0;
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
        answerDiagnostics: column.answerDiagnostics,
        progress: {
          steps: column.progressSteps,
          startedAtMs: column.startedAtMs,
          // 配信が途絶え、保存済みの回答を取り直している間は「接続を確認しています」（#1160）。
          reconnecting: streamTracker.reconnecting && column.status === "streaming",
        },
      }))
    : [];
  const lastTurnId = turns.length ? turns[turns.length - 1].user.message_id : null;

  // 会話の履歴の中身（読み込み中・失敗・0 件・一覧・ページ送り）。lg 以上はインラインのパネル、
  // lg 未満はモーダルの side sheet に入れる（どちらか一方だけを描く）。
  const historyContent = (
    <>
      {searchAnswerProfileLoading ? (
        // 検索・回答プロファイルの読み込み中は会話の一覧をまだ取得できない。「まだ会話がありません」と出さず、
        // 一覧の形だけを出す（経過時間は上のカードが出しているので重ねない。#1153）。
        <ListSkeleton rows={3} rowClassName="h-12" testId="chat-conversations-skeleton" />
      ) : conversationsQuery.isLoading ? (
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
          // パネル（lg 以上）・シート（lg 未満）の高さまで伸ばし、超えたら中をスクロールする。
          // ページ送りは一覧の下に常に見せる（#403 / #664）。
          className="min-h-0 flex-1 space-y-1 overflow-y-auto overscroll-contain"
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
    </>
  );
  const activeConversation =
    conversationQuery.data ?? conversations.find((conversation) => conversation.id === activeId);
  const activeTitle = activeConversation?.title ?? t("chat.sessions.untitled");

  return (
    <div className="flex min-h-full shrink-0 flex-col lg:h-full lg:min-h-0">
      <PageHeader wide title={t("chat.title")} subtitle={t("chat.subtitle")} />

      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4">
        {/* 検索・回答プロファイル scope */}
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {searchAnswerProfileLoading ? (
              <TimedLoadingState
                label={t("chat.searchAnswerProfile.loading")}
                operationKey="chat-search-answer-profiles-load"
                framed={false}
                testId="chat-search-answer-profiles-loading"
              >
                <SearchAnswerProfileSelectSkeleton />
              </TimedLoadingState>
            ) : searchAnswerProfilesFailed ? (
              <ApiErrorState
                error={searchAnswerProfilesQuery.error}
                fallback={t("chat.searchAnswerProfile.error")}
                onRetry={() => void searchAnswerProfilesQuery.refetch()}
              />
            ) : noSearchAnswerProfiles ? (
              <EmptyState
                title={t("chat.searchAnswerProfile.empty")}
                action={
                  <Button onClick={() => navigate(APP_ROUTES.searchAnswerProfiles)} variant="secondary">
                    {t("chat.searchAnswerProfile.open")}
                  </Button>
                }
              />
            ) : (
              <div className="space-y-4">
                {/* RAG 検索と同じ部品・文言・幅（#635）。 */}
                <SearchAnswerProfileSelect
                  id="chat-search-answer-profile"
                  items={searchAnswerProfiles}
                  value={searchAnswerProfileId}
                  onChange={setSearchAnswerProfileId}
                />
                {searchAnswerProfileWithoutKnowledgeBases && searchAnswerProfileId ? (
                  <Banner
                    severity="warning"
                    action={
                      canOpenSearchAnswerProfiles ? (
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() =>
                            navigate(
                              `${APP_ROUTES.searchAnswerProfiles}?id=${encodeURIComponent(searchAnswerProfileId)}`
                            )
                          }
                        >
                          {t("chat.searchAnswerProfile.openSettings")}
                        </Button>
                      ) : undefined
                    }
                  >
                    {t("chat.searchAnswerProfile.noKnowledgeBases")}
                  </Banner>
                ) : null}
              </div>
            )}
          </CardContent>
        </Card>

        {/* 検索・回答プロファイルの読み込み中も会話の領域を描き（寸法を予約し、読み込み後に現れて押し下げない）、
            空の状態の代わりに会話の形の Skeleton を出す（messaging.md §11.7、#1153）。 */}
        {searchAnswerProfilesFailed || noSearchAnswerProfiles ? null : !searchAnswerProfileLoading &&
          !searchAnswerProfileId ? (
          <Card className="min-h-0 flex-1">
            <CardContent className="p-4 sm:p-5">
              <EmptyState title={t("chat.searchAnswerProfile.required")} />
            </CardContent>
          </Card>
        ) : (
          // 骨格（履歴のパネル・シート、会話の領域の上端の行・会話の欄・入力欄の領域）は 3 製品共通の ChatLayout（#1161）。
          <ChatLayout
            history={history}
            historyTitle={t("chat.sessions.title")}
            historyCloseLabel={t("chat.sessions.close")}
            historyContent={historyContent}
            historyToggleDisabled={searchAnswerProfileLoading}
            label={t("chat.title")}
            // 会話を選んでいる間は、履歴を閉じていても今の会話の名前を出す。
            conversationTitle={activeId && activeConversation ? activeTitle : null}
            conversationTitleLoading={Boolean(activeId) && !activeConversation}
            newConversation={{
              label: t("chat.sessions.new"),
              onClick: () => void startNewConversation(),
              disabled: createConversation.isPending || searchAnswerProfileLoading,
            }}
            logLabel={t("chat.messages.label")}
            logRef={scrollRef}
            testIds={{ log: "chat-messages" }}
            composer={
              <>
                {compareModels.length > 0 ? (
                  <div className="flex flex-wrap items-center gap-2" data-testid="chat-answer-model">
                    {/* 候補は既定のテキストモデル（先頭。未選択のときに答える）と既定の画像対応モデル（#675）。
                        同じモデルなら 1 件で、画像対応モデルも兼ねることを名前と説明で出す（#888）。
                        説明は常設せず、ラベルの横の info アイコンから出す（#901）。 */}
                    <span className="inline-flex items-center gap-0.5">
                      <span className="text-xs font-medium text-fg-muted">{t("chat.compare.label")}</span>
                      <InfoTip
                        label={t("chat.compare.infoLabel")}
                        content={t(answerModelHelpKey(compareModels, "chat.compare.default"))}
                        contentTestId="chat-default-model"
                        data-testid="chat-answer-model-info"
                      />
                    </span>
                    {compareModels.map((model) => (
                      <ToggleChip
                        key={model.model_id}
                        selected={selectedModelIds.includes(model.model_id)}
                        onClick={() => toggleModel(model.model_id)}
                      >
                        {answerModelLabel(model)}
                      </ToggleChip>
                    ))}
                  </div>
                ) : null}
                {/* 入力欄と送信の行。送信は入力欄の下端にそろえ、375px では下に全幅で置く（#613）。 */}
                <FieldActionRow
                  actions={
                    // 送信と停止は同じボタン。生成中は同じ位置で「停止」になる（buttons.md §3.1、#413）。
                    // 主な問い合わせの入力の行なので lg（README §4「操作部品の高さと幅」）。
                    <RunStopButton
                      running={sending}
                      onRun={() => void send()}
                      onStop={stop}
                      runLabel={t("chat.composer.send")}
                      stopLabel={t("chat.composer.stop")}
                      runIcon={SendHorizontal}
                      runDisabled={
                        composer.trim().length === 0 ||
                        searchAnswerProfileWithoutKnowledgeBases ||
                        pendingChoice ||
                        prerequisitesLoading ||
                        conversationFailed
                      }
                      size="lg"
                      testId="chat-run-stop"
                    />
                  }
                >
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
                    placeholder={t("chat.composer.placeholder")}
                    // 前提の読み込み中は書けない（書いた文字は作業状態に残す。#1153）。
                    disabled={prerequisitesLoading}
                    // 生成中も入力できる（次の質問を書ける）。生成中の Enter は send が無視し、停止しない（#413）。
                    // 会話を選んでいなくても入力でき、最初の送信で会話を作る（#664）。
                    // ラベルは読み上げだけ（sr-only）なので、欄の上に余白を空けない。
                    className="space-y-0"
                  />
                </FieldActionRow>
                {errorText ? (
                  <p className="text-sm text-danger-fg" role="alert">
                    {errorText}
                  </p>
                ) : null}
              </>
            }
          >
            {/* 会話を選んでいない間は新しい会話の下書き。最初の送信で会話を作る（#664）。 */}
            {conversationLoading && !liveTurn ? (
              <TimedLoadingState
                label={t("chat.messages.loading")}
                operationKey="chat-conversation-load"
                framed={false}
                testId="chat-messages-loading"
              >
                {/* 質問と回答の吹き出しの寸法を予約する（3 製品で同じ形。#1153）。 */}
                <ChatSkeleton />
              </TimedLoadingState>
            ) : searchAnswerProfileLoading ? (
              // 検索・回答プロファイルの読み込み中は空の状態を出さず、会話の形の Skeleton だけを出す。
              // 経過時間は上のカードが出しているので重ねない（同じ取得の経過時間は 1 か所。#1153）。
              <ChatSkeleton testId="chat-messages-skeleton" />
            ) : activeId && conversationQuery.isError ? (
              <ErrorState
                message={t("chat.messages.error")}
                onRetry={() => void conversationQuery.refetch()}
              />
            ) : turns.length === 0 && !liveTurn && !pendingChoice ? (
              <EmptyState title={t("chat.messages.empty")} />
            ) : (
              <>
                {turns.map((turn) => (
                  <MessageTurn
                    key={turn.user.message_id}
                    user={turn.user}
                    searchAnswerProfileId={searchAnswerProfileId ?? ""}
                    onRetry={
                      // 最新の質問の失敗（時間切れなど）だけ、同じ質問をもう一度送れる（#375）。
                      turn.user.message_id === lastTurnId &&
                      turn.user.status !== "ERROR" &&
                      !liveTurn &&
                      !sending
                        ? () => void send(turn.user.content)
                        : undefined
                    }
                    onAskUnscoped={
                      turn.user.message_id === lastTurnId && !liveTurn && !sending && !pendingChoice
                        ? () => askUnscoped(turn.user.content)
                        : undefined
                    }
                    columns={turn.replies.map((reply) => ({
                      key: reply.message_id,
                      // 保存した回答は model_id を持つ。生成中と同じ表示名に引き直す（#649）。
                      label: reply.model ? (modelLabels.get(reply.model) ?? reply.model) : null,
                      answer: reply.content,
                      citations: reply.citations,
                      traceId: reply.trace_id,
                      messageId: reply.message_id,
                      streaming: false,
                      errorMessage: reply.status === "ERROR" ? reply.content : null,
                      guardrailWarnings: reply.guardrail_warnings,
                      savedAnswer: Boolean(
                        reply.trace_id && answerTraceIds.has(reply.trace_id)
                      ),
                      // この画面で送った回答は、処理の経過を回答の上に残す（#1146）。
                      progress: finishedProgress[reply.message_id]
                        ? { steps: finishedProgress[reply.message_id], startedAtMs: 0 }
                        : null,
                    }))}
                  />
                ))}
                {liveTurn ? (
                  // 送った質問はサーバーの応答を待たずに出し、失敗・停止のときも残す（#907）。
                  <MessageTurn
                    user={liveTurn.user ?? { content: liveTurn.pending.content, guardrail_warnings: [] }}
                    userStatus={
                      liveTurn.pending.status === "sending"
                        ? liveTurn.user
                          ? "sent"
                          : "sending"
                        : liveTurn.pending.status
                    }
                    columns={liveColumns}
                    searchAnswerProfileId={searchAnswerProfileId ?? ""}
                    testId="chat-live-turn"
                    footer={
                      liveTurn.pending.status === "failed" ? (
                        <div data-testid="chat-send-failure">
                          <Banner
                            severity="danger"
                            action={
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                icon={RotateCcw}
                                disabled={sending || pendingChoice}
                                onClick={resend}
                              >
                                {t("chat.send.retry")}
                              </Button>
                            }
                          >
                            {liveTurn.failureMessage ?? t("chat.send.failedHint")}
                          </Banner>
                        </div>
                      ) : liveTurn.pending.status === "stopped" ? (
                        // 止めた状態。色だけに頼らず、停止のアイコンを添える（Agent のチャットと同じ）。
                        <p
                          className="flex items-center gap-1.5 text-sm text-fg-muted"
                          data-testid="chat-stopped"
                        >
                          <Square size={14} aria-hidden="true" />
                          {t("chat.send.stopped")}
                        </p>
                      ) : null
                    }
                  />
                ) : null}
                {/* 送る前の質問と、選んでから回答する類似問（#684）。質問の吹き出しは送信後と同じ形。 */}
                {faqChoice ? (
                  <div className="space-y-2" data-testid="chat-approved-faq-choice">
                    <ChatUserMessage>{faqChoice.content}</ChatUserMessage>
                    <ApprovedFaqSuggestions
                      mode="chat"
                      suggestions={faqChoice.suggestions}
                      onUse={(suggestion) => chooseFaq(suggestion.id)}
                      onSkip={() => chooseFaq()}
                    />
                  </div>
                ) : null}
                {/* 類似問の後に出す確認の質問（#717）。 */}
                {clarifyChoice ? (
                  <div className="space-y-2">
                    <ChatUserMessage>{clarifyChoice.content}</ChatUserMessage>
                    <ClarificationChoice
                      suggestion={clarifyChoice.suggestion}
                      onAnswer={(answer) => chooseClarification(answer)}
                      onSkip={() => chooseClarification()}
                    />
                  </div>
                ) : null}
              </>
            )}
          </ChatLayout>
        )}
      </PageBody>
    </div>
  );
}

interface FaqChoice {
  content: string;
  retryContent?: string;
  suggestions: ApprovedFaqSuggestionData[];
}

/** 保存した類似問の選択待ちが今の形か（古い版・壊れた値は捨てる。#702）。 */
function isFaqChoice(value: unknown): value is FaqChoice | null {
  if (value === null) return true;
  if (typeof value !== "object") return false;
  const choice = value as Partial<FaqChoice>;
  return (
    typeof choice.content === "string" &&
    (choice.retryContent === undefined || typeof choice.retryContent === "string") &&
    Array.isArray(choice.suggestions) &&
    choice.suggestions.every(
      (item) =>
        typeof item === "object" &&
        item !== null &&
        typeof item.id === "string" &&
        typeof item.question === "string" &&
        typeof item.answer === "string" &&
        typeof item.score === "number"
    )
  );
}

interface ClarifyChoice {
  content: string;
  retryContent?: string;
  suggestion: ClarificationSuggestionData;
}

/** 保存した確認の選択待ちが今の形か（古い版・壊れた値は捨てる。#717）。 */
function isClarifyChoice(value: unknown): value is ClarifyChoice | null {
  if (value === null) return true;
  if (typeof value !== "object") return false;
  const choice = value as Partial<ClarifyChoice>;
  const suggestion = choice.suggestion as Partial<ClarificationSuggestionData> | undefined;
  return (
    typeof choice.content === "string" &&
    (choice.retryContent === undefined || typeof choice.retryContent === "string") &&
    typeof suggestion === "object" &&
    suggestion !== null &&
    typeof suggestion.rule_id === "string" &&
    typeof suggestion.clarification === "object" &&
    suggestion.clarification !== null &&
    Array.isArray(suggestion.clarification.options)
  );
}
