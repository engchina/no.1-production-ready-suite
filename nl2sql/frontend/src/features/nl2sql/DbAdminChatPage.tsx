import { useCallback, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Play, Sparkles } from "lucide-react";
import {
  ApiErrorBanner,
  Banner,
  Button,
  ChatAnswer,
  ChatProgress,
  ChatTurn,
  EmptyState,
  ExecutionConfirmationField,
  MessageText,
  PageBody,
  PageHeader,
  ProcessingIndicator,
  StatusBadge,
  createOptimisticChatMessage,
  useChatAutoScroll,
  withOptimisticChatStatus,
  type OptimisticChatMessage,
} from "@production-ready/ui";

import { useWorkspaceState } from "@/components/WorkspaceState";
import { ApiError, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { API_TIMEOUT_MS } from "@/lib/requestPolicy";

import { CHAT_PROGRESS_LABELS } from "./chatProgress";
import {
  ChatSqlBlock,
  Nl2SqlChatComposer,
  Nl2SqlChatLayout,
  Nl2SqlPendingTurn,
} from "./components/ChatScaffold";
import { ChatQueryResult } from "./components/ChatSqlExecution";
import { DbAdminExecutionResult } from "./components/DbAdminShared";
import { SchemaSyncNotice } from "./components/SchemaRefreshFeedback";
import { DEFAULT_SQL_ROW_LIMIT } from "./components/SqlRowLimitControls";
import {
  DB_ADMIN_CHAT_TURN_LIMIT,
  dbAdminChatCanExecute,
  dbAdminChatConfirmed,
  dbAdminChatGenerateStep,
  dbAdminChatHistory,
  dbAdminChatProgressSteps,
  dbAdminExecutionSucceeded,
  dbAdminFailureText,
  dbAdminOutcomeSummary,
  type DbAdminChatExecution,
} from "./dbAdminChat";
import { nl2sqlIncrementalKeys } from "./incrementalQueries";
import type {
  DbAdminChatAnalyzeData,
  DbAdminChatGenerateData,
  DbAdminChatPlan,
  DbAdminExecuteData,
} from "./types";
import { useMutationSchemaSync } from "./useMutationSchemaSync";

/** 画面の中だけの 1 往復（保存しない。画面を離れると消える。#2264）。 */
interface DbAdminChatTurnData {
  id: string;
  question: string;
  sentAtMs: number;
  generatedAtMs: number;
  explanation: string;
  warnings: string[];
  contextTables: string[];
  /** 今の SQL の分類（AI のエラー分析で直した SQL に置き換えると revision を進める）。 */
  plan: DbAdminChatPlan;
  revision: number;
  corrected: boolean;
  /** 続きの質問の文脈に渡す、実行の結果の短い要約。 */
  outcome: string;
}

interface PendingGenerate {
  message: OptimisticChatMessage;
  error: unknown;
}

const TEST_ID = "db-admin-chat";

/**
 * DB 管理チャット（#2264）。質問から Oracle の SQL（SELECT・DML・DDL・DCL・PL/SQL）を Enterprise AI Direct で
 * 作り、生成した SQL を必ず見せる。SELECT は「実行」で、それ以外は実行確認語を入力して実行する（自動では
 * 実行しない）。画面の骨格・入力欄・送信中の往復・SQL の表示・結果の表・処理の経過は「チャット」と同じ部品
 * （ChatScaffold・ChatQueryResult・共通の ChatProgress）で描く。会話は保存しない。
 */
export function DbAdminChatPage() {
  const [draft, setDraft] = useWorkspaceState("draft", "");
  const [turns, setTurns] = useState<DbAdminChatTurnData[]>([]);
  const [pending, setPending] = useState<PendingGenerate | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const generating = pending?.message.status === "sending";

  const autoScroll = useChatAutoScroll({
    contentKey: [
      turns.length,
      turns.map((turn) => `${turn.revision}:${turn.outcome}`).join(","),
      pending?.message.localId ?? "",
      pending?.message.status ?? "",
    ].join(":"),
  });

  const generate = useCallback(
    async (question: string, message: OptimisticChatMessage) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setPending({ message, error: null });
      try {
        const data = await apiPost<DbAdminChatGenerateData>(
          "/api/nl2sql/db-admin-chat/generate",
          {
            question,
            history: dbAdminChatHistory(
              turns.map((turn) => ({ question: turn.question, sql: turn.plan.sql, outcome: turn.outcome })),
            ),
          },
          { signal: controller.signal, timeoutMs: API_TIMEOUT_MS.longRunningJob },
        );
        setTurns((previous) => [
          ...previous,
          {
            id: message.localId,
            question,
            sentAtMs: message.sentAtMs,
            generatedAtMs: Date.now(),
            explanation: data.explanation,
            warnings: data.warnings,
            contextTables: data.context_tables,
            plan: data.plan,
            revision: 0,
            corrected: false,
            outcome: "",
          },
        ]);
        setPending(null);
      } catch (error) {
        if (controller.signal.aborted) {
          // 停止した質問は入力欄に戻す（生成は副作用が無いので、送り直せる）。
          setPending(null);
          setDraft(question);
          return;
        }
        setPending({ message: withOptimisticChatStatus(message, "failed"), error });
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [turns, setDraft],
  );

  const submit = () => {
    const question = draft.trim();
    if (!question || generating || turns.length >= DB_ADMIN_CHAT_TURN_LIMIT) return;
    setDraft("");
    void generate(question, createOptimisticChatMessage(question));
  };

  const resend = () => {
    if (!pending || generating) return;
    void generate(pending.message.content, withOptimisticChatStatus(pending.message, "sending"));
  };

  const reset = () => {
    abortRef.current?.abort();
    setTurns([]);
    setPending(null);
    composerRef.current?.focus();
  };

  const replacePlan = useCallback((turnId: string, plan: DbAdminChatPlan) => {
    setTurns((previous) =>
      previous.map((turn) =>
        turn.id === turnId
          ? { ...turn, plan, revision: turn.revision + 1, corrected: true, outcome: "" }
          : turn,
      ),
    );
  }, []);

  const recordOutcome = useCallback((turnId: string, outcome: string) => {
    setTurns((previous) =>
      previous.map((turn) => (turn.id === turnId ? { ...turn, outcome } : turn)),
    );
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <PageHeader wide title={t("nav.dbAdminChat")} subtitle={t("dbAdminChat.subtitle")} />
      <PageBody wide className="flex min-h-0 flex-1 flex-col gap-4" data-testid="nl2sql-db-admin-chat">
        <Nl2SqlChatLayout
          label={t("dbAdminChat.conversation")}
          onNewConversation={reset}
          newConversationDisabled={turns.length === 0 && !pending}
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
              submitBlocked={turns.length >= DB_ADMIN_CHAT_TURN_LIMIT}
              sendTestId={`${TEST_ID}-send`}
              footer={
                turns.length >= DB_ADMIN_CHAT_TURN_LIMIT ? (
                  <Banner severity="info">{t("chat.limit")}</Banner>
                ) : null
              }
            />
          }
        >
          {turns.length === 0 && !pending ? (
            <EmptyState title={t("chat.empty")} hint={t("dbAdminChat.emptyHint")} />
          ) : null}
          {turns.map((turn) => (
            <DbAdminChatTurn
              key={`${turn.id}:${turn.revision}`}
              turn={turn}
              onReplacePlan={replacePlan}
              onOutcome={recordOutcome}
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
      </PageBody>
    </div>
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

/** 1 往復の回答（生成した SQL・確認語・実行の結果・失敗の分析）。 */
function DbAdminChatTurn({
  turn,
  onReplacePlan,
  onOutcome,
}: {
  turn: DbAdminChatTurnData;
  onReplacePlan: (turnId: string, plan: DbAdminChatPlan) => void;
  onOutcome: (turnId: string, outcome: string) => void;
}) {
  const queryClient = useQueryClient();
  const { plan } = turn;
  const [confirmation, setConfirmation] = useState("");
  const [execution, setExecution] = useState<DbAdminChatExecution | null>(null);
  const [executeError, setExecuteError] = useState<unknown>(null);
  const [analysis, setAnalysis] = useState<AnalysisState | null>(null);
  const [copyError, setCopyError] = useState("");
  const refreshSchemaReadModels = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["schema", "objects"] });
    void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
    void queryClient.invalidateQueries({ queryKey: nl2sqlIncrementalKeys.schemaHead });
  }, [queryClient]);
  // DDL の後の DB 構造の同期の案内と進行は、実行の結果の直下に出す（管理 SQL を実行と同じ。#2031）。
  const schemaSync = useMutationSchemaSync({
    noticeOrigin: `db-admin-chat-notice-${turn.id}`,
    onDone: refreshSchemaReadModels,
  });
  const running = execution?.status === "running";
  const result = execution?.result ?? null;
  const executed = Boolean(result && dbAdminExecutionSucceeded(result));
  const failed = Boolean(
    execution && execution.status === "failed" && (executeError || (result && !dbAdminExecutionSucceeded(result))),
  );
  const canExecute = dbAdminChatCanExecute(plan, confirmation) && !running;

  const execute = async () => {
    if (!canExecute) return;
    const startedAtMs = Date.now();
    setExecution({ status: "running", startedAtMs });
    setExecuteError(null);
    setAnalysis(null);
    schemaSync.reset();
    try {
      const data = await apiPost<DbAdminExecuteData>("/api/nl2sql/db-admin-chat/execute", {
        sql: plan.sql,
        confirmation: plan.requires_confirmation ? confirmation.trim() : "",
        row_limit: DEFAULT_SQL_ROW_LIMIT,
      });
      const ok = dbAdminExecutionSucceeded(data);
      setExecution({ status: ok ? "done" : "failed", startedAtMs, finishedAtMs: Date.now(), result: data });
      onOutcome(turn.id, dbAdminOutcomeSummary(data));
      // 実行の確認は 1 回ごと（もう一度実行するときは入力し直す）。
      setConfirmation("");
      const tracked = schemaSync.track(data);
      if (data.committed && !tracked) {
        void queryClient.invalidateQueries({ queryKey: ["nl2sql", "db-admin", "objects"] });
      }
    } catch (error) {
      setExecution({ status: "failed", startedAtMs, finishedAtMs: Date.now(), result: null });
      setExecuteError(error);
      onOutcome(
        turn.id,
        t("dbAdminChat.outcome.failed", {
          message: error instanceof Error ? error.message : t("dbAdminChat.execute.failed"),
        }),
      );
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
    <ChatTurn question={turn.question} testId={`${TEST_ID}-turn`}>
      <ChatAnswer>
        <ChatProgress
          steps={dbAdminChatProgressSteps({
            sentAtMs: turn.sentAtMs,
            generatedAtMs: turn.generatedAtMs,
            plan,
            execution,
          })}
          startedAt={turn.sentAtMs}
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
        {turn.contextTables.length > 0 ? (
          <p className="break-words text-xs text-fg-muted" data-testid={`${TEST_ID}-context-tables`}>
            {t("dbAdminChat.contextTables", { tables: turn.contextTables.join(", ") })}
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
              id={`${TEST_ID}-confirmation-${turn.id}`}
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
                operationKey={`db-admin-chat-execute-${turn.id}`}
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
            {!running && result && !(result.select_result && executed) ? (
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
          <AnalysisPanel
            analysis={analysis}
            turnId={turn.id}
            onUseCorrected={(corrected) => onReplacePlan(turn.id, corrected)}
          />
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
  onUseCorrected: (plan: DbAdminChatPlan) => void;
}) {
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
                    onClick={() => {
                      if (analysis.data.corrected_plan) onUseCorrected(analysis.data.corrected_plan);
                    }}
                    data-testid={`${TEST_ID}-use-corrected`}
                  >
                    {t("dbAdminChat.analyze.useCorrected")}
                  </Button>
                }
              />
              <p className="text-xs text-fg-muted">{t("dbAdminChat.analyze.correctedHint")}</p>
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
