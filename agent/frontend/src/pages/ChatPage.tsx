import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, History, MessageSquarePlus, SendHorizontal, Wrench, X } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  Disclosure,
  EmptyState,
  FieldActionRow,
  ListSkeleton,
  MessageText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  SearchableSelectField,
  SideSheet,
  Skeleton,
  StatusBadge,
  TextareaField,
  TimedLoadingState,
  isSubmitEnter,
  toast,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import {
  agentApi,
  type ApprovalRequest,
  type Artifact,
  type RunState,
  type RunStep,
  type ThreadSummary,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useCapabilities } from "@/lib/permissions";
import { isNullableString, isString, useWorkspaceState } from "@/lib/workspace-state";

/**
 * 業務利用者のチャット（#768）。使ってよい業務 Agent を選んで会話する。1 往復が 1 Run で、
 * 同じ会話の前の質問と回答は組み込み Runtime がモデルへ渡す。回答の下に出典・使ったツール・
 * 承認待ちを出す。画面の型は RAG のチャットと同じ（左に会話の一覧、右に会話と入力欄）。
 */

const ACTIVE_STATUSES = new Set<RunState["status"]>(["queued", "running"]);
const POLL_INTERVAL_MS = 1500;

function useHistoryInline(): boolean {
  // lg（1024px）以上は会話の一覧を左に並べ、未満は「会話の履歴」で開く side sheet にする（RAG と同じ）。
  const query = "(min-width: 1024px)";
  const [inline, setInline] = useState(() =>
    typeof window === "undefined" ? true : window.matchMedia(query).matches
  );
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setInline(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return inline;
}

export function ChatPage() {
  const queryClient = useQueryClient();
  const capabilities = useCapabilities();
  const historyInline = useHistoryInline();
  const [historySheetOpen, setHistorySheetOpen] = useState(false);
  const historyToggleRef = useRef<HTMLButtonElement | null>(null);
  const [agentId, setAgentId] = useWorkspaceState("chat", "agentId", "", isString);
  const [threadId, setThreadId] = useWorkspaceState<"chat", string | null>(
    "chat",
    "threadId",
    null,
    isNullableString
  );
  const [draft, setDraft] = useWorkspaceState("chat", "draft", "", isString);
  const conversationEndRef = useRef<HTMLDivElement | null>(null);

  const agents = useQuery({ queryKey: ["agents"], queryFn: agentApi.listAgents });
  const usableAgents = useMemo(
    () => (agents.data?.agents ?? []).filter((agent) => agent.enabled && !agent.migration_required),
    [agents.data]
  );
  // 選んだ Agent が使えなくなっていたら、先頭の Agent にする。
  const selectedAgent = usableAgents.find((agent) => agent.id === agentId) ?? usableAgents[0];
  const selectedAgentId = selectedAgent?.id ?? "";

  const threads = useQuery({
    queryKey: ["threads", selectedAgentId],
    queryFn: () => agentApi.listThreads(selectedAgentId),
    enabled: Boolean(selectedAgentId),
  });
  const thread = useQuery({
    queryKey: ["thread", threadId],
    queryFn: () => agentApi.getThread(threadId ?? ""),
    enabled: Boolean(threadId),
    retry: false,
    // 実行中の Run があるあいだだけ取り直す（終わったら止める）。
    refetchInterval: (query) =>
      (query.state.data?.runs ?? []).some((run) => ACTIVE_STATUSES.has(run.status))
        ? POLL_INTERVAL_MS
        : false,
  });
  // 選んでいた会話が消えた・別の Agent の会話なら、新しい会話に戻す。
  const threadMissing = Boolean(threadId) && thread.isError;
  const threadOfOtherAgent =
    thread.data !== undefined && selectedAgentId !== "" && thread.data.agent_id !== selectedAgentId;
  useEffect(() => {
    if (threadMissing || threadOfOtherAgent) setThreadId(null);
  }, [threadMissing, threadOfOtherAgent, setThreadId]);

  const runs = threadId && !threadOfOtherAgent ? (thread.data?.runs ?? []) : [];
  const running = runs.some((run) => ACTIVE_STATUSES.has(run.status));
  const waitingApproval = runs.some((run) => run.status === "waiting_approval");
  const lastRunId = runs.at(-1)?.id;
  const lastRunStatus = runs.at(-1)?.status;

  useEffect(() => {
    conversationEndRef.current?.scrollIntoView({ block: "end" });
  }, [lastRunId, lastRunStatus]);

  const send = useMutation({
    mutationFn: (goal: string) =>
      agentApi.createRun({
        goal,
        agent_id: selectedAgentId,
        thread_id: threadId ?? undefined,
      }),
    onSuccess: (run) => {
      setDraft("");
      if (run.thread_id) setThreadId(run.thread_id);
      void queryClient.invalidateQueries({ queryKey: ["thread", run.thread_id] });
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
    },
  });

  const decide = useMutation({
    mutationFn: ({ approval, approved }: { approval: ApprovalRequest; approved: boolean }) =>
      agentApi.decideApproval(approval.id, { approved }),
    onSuccess: (_run, { approved }) => {
      toast.success(approved ? t("chat.approval.approved") : t("chat.approval.rejected"));
      void queryClient.invalidateQueries({ queryKey: ["thread", threadId] });
    },
    onError: (error) => toast.error(error.message),
  });

  function submit() {
    const goal = draft.trim();
    // 実行中・承認待ちのあいだは次の質問を送らない（前の回答を会話の履歴に含めるため）。
    if (!goal || !selectedAgentId || running || waitingApproval || send.isPending) return;
    send.mutate(goal);
  }

  function startNewThread() {
    setThreadId(null);
    send.reset();
    setHistorySheetOpen(false);
  }

  function openThread(item: ThreadSummary) {
    setThreadId(item.thread_id);
    send.reset();
    setHistorySheetOpen(false);
  }

  const historyContent = (
    <ThreadList
      threads={threads.data?.threads ?? []}
      loading={threads.isLoading}
      currentThreadId={threadOfOtherAgent ? null : threadId}
      onOpen={openThread}
      onNew={startNewThread}
    />
  );

  const composerBlocked = running || waitingApproval;

  return (
    <div className="flex min-h-full flex-col lg:h-full lg:min-h-0">
      <PageHeader wide title={t("chat.title")} subtitle={t("chat.subtitle")} />
      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4">
        <Card className="shrink-0">
          <CardContent className="p-4 sm:p-5">
            {agents.isLoading ? (
              <TimedLoadingState label={t("chat.agent.loading")} framed={false} testId="chat-agents-loading">
                <Skeleton className="h-10 w-full" />
              </TimedLoadingState>
            ) : agents.isError ? (
              <Banner severity="danger">{agents.error.message}</Banner>
            ) : usableAgents.length === 0 ? (
              <EmptyState title={t("chat.agent.empty")} />
            ) : (
              // 画面の対象を決める主な選択欄（RAG の業務ビューと同じく full）。
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
                  send.reset();
                }}
                width="full"
              />
            )}
          </CardContent>
        </Card>

        {selectedAgentId ? (
          <div
            className={
              historyInline
                ? "grid min-w-0 gap-4 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,17.5rem)_minmax(0,1fr)]"
                : "grid min-w-0 gap-4"
            }
          >
            {historyInline ? (
              <aside
                aria-label={t("chat.threads.title")}
                data-testid="chat-history"
                className="flex min-h-0 min-w-0 flex-col gap-3 rounded-lg border border-border bg-surface px-3 pb-3 pt-2 shadow-sm"
              >
                <h2 className="flex min-h-8 items-center px-1 text-sm font-medium text-fg">
                  {t("chat.threads.title")}
                </h2>
                {historyContent}
              </aside>
            ) : (
              <SideSheet
                open={historySheetOpen}
                onClose={() => setHistorySheetOpen(false)}
                title={t("chat.threads.title")}
                closeLabel={t("chat.threads.close")}
                id="chat-history-sheet"
                returnFocusRef={historyToggleRef}
                bodyClassName="gap-3"
                data-testid="chat-history"
              >
                {historyContent}
              </SideSheet>
            )}

            <section
              aria-label={t("chat.conversation")}
              className="flex min-h-[28rem] min-w-0 flex-col rounded-lg border border-border bg-surface shadow-sm lg:min-h-0"
            >
              <div className="flex min-h-12 items-center justify-between gap-2 border-b border-border px-3 py-2">
                <h2 className="min-w-0 truncate text-sm font-medium text-fg">
                  {threadId && runs[0] ? runs[0].goal : t("chat.newConversation")}
                </h2>
                <div className="flex shrink-0 items-center gap-2">
                  {historyInline ? null : (
                    <Button
                      ref={historyToggleRef}
                      variant="secondary"
                      size="sm"
                      icon={History}
                      aria-controls="chat-history-sheet"
                      aria-expanded={historySheetOpen}
                      onClick={() => setHistorySheetOpen(true)}
                    >
                      {t("chat.threads.open")}
                    </Button>
                  )}
                  <Button variant="secondary" size="sm" icon={MessageSquarePlus} onClick={startNewThread}>
                    {t("chat.threads.new")}
                  </Button>
                </div>
              </div>

              <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4" data-testid="chat-conversation">
                {threadId && thread.isLoading ? (
                  <TimedLoadingState label={t("chat.loading")} framed={false} testId="chat-thread-loading">
                    <ListSkeleton rows={3} />
                  </TimedLoadingState>
                ) : runs.length === 0 ? (
                  <EmptyState title={t("chat.empty.title")} hint={t("chat.empty.hint")} />
                ) : (
                  runs.map((run) => (
                    <ChatTurn
                      key={run.id}
                      run={run}
                      canDecide={capabilities.decideApprovals}
                      deciding={decide.isPending}
                      onDecide={(approval, approved) => decide.mutate({ approval, approved })}
                    />
                  ))
                )}
                <div ref={conversationEndRef} />
              </div>

              <div className="space-y-2 border-t border-border p-3">
                {/* 入力欄と送信の行。送信は入力欄の下端にそろえ、375px では下に全幅で置く（#613）。 */}
                <FieldActionRow
                  actions={
                    // 主な問い合わせの入力の行なので lg（README §4「操作部品の高さと幅」）。
                    <Button
                      size="lg"
                      icon={SendHorizontal}
                      loading={send.isPending}
                      disabled={!draft.trim() || composerBlocked}
                      onClick={submit}
                      data-testid="chat-send"
                    >
                      {t("chat.send")}
                    </Button>
                  }
                >
                  <TextareaField
                    id="chat-composer"
                    label={t("chat.composer.label")}
                    labelHidden
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      // IME の変換を確定する Enter では送信しない（#459）。Shift+Enter は改行。
                      if (isSubmitEnter(event) && !event.shiftKey) {
                        event.preventDefault();
                        submit();
                      }
                    }}
                    rows={2}
                    placeholder={t("chat.composer.placeholder")}
                    className="space-y-0"
                  />
                </FieldActionRow>
                {composerBlocked ? (
                  <p className="text-xs text-fg-muted" data-testid="chat-composer-hint">
                    {waitingApproval ? t("chat.composer.waitingApproval") : t("chat.composer.running")}
                  </p>
                ) : null}
                {send.error ? <Banner severity="danger">{send.error.message}</Banner> : null}
              </div>
            </section>
          </div>
        ) : null}
      </PageBody>
    </div>
  );
}

function ThreadList({
  threads,
  loading,
  currentThreadId,
  onOpen,
  onNew,
}: {
  threads: ThreadSummary[];
  loading: boolean;
  currentThreadId: string | null;
  onOpen: (thread: ThreadSummary) => void;
  onNew: () => void;
}) {
  if (loading) {
    return (
      <TimedLoadingState label={t("chat.threads.loading")} framed={false} testId="chat-threads-loading">
        <ListSkeleton rows={4} />
      </TimedLoadingState>
    );
  }
  if (threads.length === 0) {
    return (
      <EmptyState
        title={t("chat.threads.empty")}
        action={
          <Button variant="secondary" size="sm" icon={MessageSquarePlus} onClick={onNew}>
            {t("chat.threads.new")}
          </Button>
        }
      />
    );
  }
  return (
    <ul className="min-h-0 space-y-1 overflow-y-auto" aria-label={t("chat.threads.title")}>
      {threads.map((item) => {
        const current = item.thread_id === currentThreadId;
        return (
          <li key={item.thread_id}>
            <button
              type="button"
              onClick={() => onOpen(item)}
              aria-current={current ? "true" : undefined}
              className={
                current
                  ? "flex w-full flex-col gap-1 rounded-md bg-accent-subtle px-2 py-2 text-left"
                  : "flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left hover:bg-surface-hover"
              }
            >
              <span className="line-clamp-2 break-words text-sm text-fg">{item.title}</span>
              <span className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                <span>{formatTime(item.updated_at)}</span>
                <span>{t("chat.threads.turns", { count: item.run_count })}</span>
                {item.last_status === "completed" ? null : (
                  <StatusBadge variant={runStatusVariant(item.last_status)} label={t(`chat.status.${item.last_status}`)} />
                )}
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function ChatTurn({
  run,
  canDecide,
  deciding,
  onDecide,
}: {
  run: RunState;
  canDecide: boolean;
  deciding: boolean;
  onDecide: (approval: ApprovalRequest, approved: boolean) => void;
}) {
  const answer = answerText(run.artifacts);
  const citations = runCitations(run.artifacts);
  const toolSteps = run.steps.filter((step) => step.tool_call);
  const pendingApprovals = run.approvals.filter((approval) => approval.status === "pending");
  const failure = run.status === "failed" ? failureMessage(run) : null;

  return (
    <div className="space-y-2" data-testid={`chat-turn-${run.id}`}>
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-md bg-accent-subtle px-3 py-2 text-sm text-fg">
          {run.goal}
        </div>
      </div>
      <div className="space-y-3 rounded-md border border-border p-3" aria-live="polite">
        {ACTIVE_STATUSES.has(run.status) ? (
          <ProcessingIndicator active operationKey={`chat-run-${run.id}`} label={t("chat.answering")} testId="chat-answering" />
        ) : null}
        {answer ? <MessageText text={answer} className="text-sm text-fg" /> : null}
        {failure ? <Banner severity="danger" title={t("chat.failed")}>{failure}</Banner> : null}
        {run.status === "cancelled" ? <p className="text-sm text-fg-muted">{t("chat.cancelled")}</p> : null}

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
                <li key={`${citation.title}-${index}`} className="rounded-sm bg-surface-sunken p-2 text-xs">
                  <p className="font-medium text-fg">
                    {index + 1}. {citation.title}
                  </p>
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
      </div>
    </div>
  );
}

function ToolStepRow({ step }: { step: RunStep }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 text-xs">
      <span className="min-w-0 break-all font-mono text-fg">{step.tool_call?.name}</span>
      <StatusBadge variant={stepStatusVariant(step.status)} label={t(`chat.step.${step.status}`)} />
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

interface CitationView {
  title: string;
  text: string;
}

/** RAG の結果（`rag_evidence` の成果物）の引用。 */
function runCitations(artifacts: Artifact[]): CitationView[] {
  const citations: CitationView[] = [];
  for (const artifact of artifacts) {
    if (artifact.kind !== "rag_evidence") continue;
    const items = artifact.content.citations;
    if (!Array.isArray(items)) continue;
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      const title =
        (typeof record.file_name === "string" && record.file_name) ||
        (typeof record.title === "string" && record.title) ||
        (typeof record.document_id === "string" && record.document_id) ||
        t("chat.sourceUntitled");
      citations.push({ title, text: typeof record.text === "string" ? record.text : "" });
    }
  }
  return citations;
}

function failureMessage(run: RunState): string {
  const event = [...run.events].reverse().find((item) => item.type === "runtime.failed");
  return event?.message || t("chat.failedUnknown");
}

function runStatusVariant(status: RunState["status"]): StatusVariant {
  if (status === "failed") return "danger";
  if (status === "waiting_approval") return "warning";
  if (status === "cancelled") return "neutral";
  if (status === "completed") return "success";
  return "info";
}

function stepStatusVariant(status: RunStep["status"]): StatusVariant {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "waiting_approval") return "warning";
  if (status === "cancelled") return "neutral";
  return "info";
}

function formatTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
