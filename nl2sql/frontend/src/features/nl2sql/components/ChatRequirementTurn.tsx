import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { CheckCircle2, MessageSquareText, Play, RotateCcw, Sparkles, X } from "lucide-react";

import {
  ApiErrorBanner,
  Banner,
  Button,
  ChatAnswer,
  ChatProgress,
  ChatTurn,
  StatusBadge,
} from "@production-ready/ui";

import { t } from "@/lib/i18n";
import { randomUuid } from "@/lib/randomUuid";

import {
  applyQuerySessionRequirement,
  cancelQuerySession,
  createQuerySession,
  getQuerySession,
} from "../ontology/api";
import type { QuerySession } from "../ontology/types";
import { CHAT_PROGRESS_LABELS } from "../chatProgress";
import {
  chatRequirementIdempotencyKey,
  chatRequirementSessionRequest,
  chatRequirementStep,
  isRequirementCheckUnavailable,
  type ChatRequirementCheck,
} from "../chatRequirement";
import {
  isSessionOpen,
  RequirementAnswerProgress,
  RequirementItemsList,
  RequirementQuestionForm,
  requirementPhaseBadge,
  RequirementStateBanners,
  RequirementSummary,
  useRequirementSession,
  type RequirementSessionController,
} from "./RequirementClarification";

/**
 * チャットの AI要件確認の段階。
 *
 * - `checking`: 要件の検査（session の作成・作業状態から戻した session の読み込み・確認の要らない質問の反映）。
 * - `failed`: 要件の検査ができなかった（もう一度確かめる・確認せずに SQL を生成する・中止を選ぶ）。
 * - `ask` / `summary` / `ended`: 会話の中の確認（1 問ずつの質問 / まとめ / 終わった確認）。
 */
export type ChatRequirementPhase = "idle" | "checking" | "failed" | "ask" | "summary" | "ended";

export interface ChatRequirementFlow {
  check: ChatRequirementCheck | null;
  controller: RequirementSessionController;
  phase: ChatRequirementPhase;
  checkError: unknown;
  /** 要件の検査の最中（入力欄の送信のボタンは「停止」になり、押すと確認を中止する）。 */
  running: boolean;
  /** もう一度確かめる（新しい session を作る）。 */
  retry: () => void;
  /** 確認せずに SQL を生成する（要件の検査ができなかったとき）。 */
  skip: () => void;
  /** 確認を中止する（質問は入力欄に戻す）。 */
  cancel: () => void;
  /** まとめを確かめた要件で SQL を生成する。 */
  generate: () => void;
  /** 会話・業務プロファイルを変えたときに、確認を外す（サーバーの session は中止する）。 */
  discard: () => void;
}

interface ChatRequirementFlowOptions {
  check: ChatRequirementCheck | null;
  setCheck: (check: ChatRequirementCheck | null) => void;
  /**
   * SQL の生成のジョブを投入する（`sessionId` が無ければ確認せずに作る）。`requirementQuestion` は確かめた
   * 質問の文（回答の「AI要件確認で確かめた質問」を、会話の取り直しを待たずに出す）。
   */
  onSubmit: (
    check: ChatRequirementCheck,
    sessionId: string | null,
    requirementQuestion?: string
  ) => void;
  /** 確認を中止した（送った質問を入力欄に戻す）。 */
  onCancelled: (check: ChatRequirementCheck) => void;
  /** 画面が表示されている（keep-alive で隠れている間は検査を始めない）。 */
  enabled: boolean;
}

/**
 * チャットで質問を送ったときの、SQL を作る前の要件の検査と会話の中の確認（#1442）。session の作成・回答・直し・
 * 反映・中止は SQL 生成の画面の AI要件確認と同じ API・同じ useRequirementSession で行う。
 */
export function useChatRequirementFlow({
  check,
  setCheck,
  onSubmit,
  onCancelled,
  enabled,
}: ChatRequirementFlowOptions): ChatRequirementFlow {
  const controller = useRequirementSession();
  const { session, setSession, setError } = controller;
  const [creating, setCreating] = useState(false);
  const [directing, setDirecting] = useState(false);
  const [checkError, setCheckError] = useState<unknown>(null);
  // 検査を始めた送信（job ID）。StrictMode・再レンダーで二重に始めない。
  const startedRef = useRef("");
  // 作成の応答の前に中止した送信（応答が届いたら session を中止する）。
  const abandonedRef = useRef(new Set<string>());
  const checkRef = useRef(check);
  const onSubmitRef = useRef(onSubmit);
  useLayoutEffect(() => {
    checkRef.current = check;
    onSubmitRef.current = onSubmit;
  });
  const clientJobId = check?.request.client_job_id ?? "";

  /** 作った・読み直した session で次へ進む（確認が要らなければ聞かずに反映して SQL を作る）。 */
  const proceed = useCallback(
    async (current: ChatRequirementCheck, loaded: QuerySession) => {
      const step = chatRequirementStep(loaded);
      if (step === "applied") {
        onSubmitRef.current(current, loaded.id, loaded.requirement?.clarified_question_ja);
        return;
      }
      if (step !== "direct") {
        setSession(loaded);
        return;
      }
      setDirecting(true);
      try {
        const applied = await applyQuerySessionRequirement(loaded.id, {
          base_version: loaded.current_intent_version ?? 1,
        });
        if (checkRef.current?.request.client_job_id !== current.request.client_job_id) return;
        onSubmitRef.current(
          current,
          applied.id,
          applied.requirement?.clarified_question_ja ?? loaded.requirement?.clarified_question_ja
        );
      } catch (cause) {
        // 反映できなければ、まとめを出して「SQL を生成」でやり直せるようにする。
        setSession(loaded);
        setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.apply"));
      } finally {
        setDirecting(false);
      }
    },
    [setError, setSession]
  );

  // 送った質問の要件の検査を始める（作業状態から戻したときは、作った session を読み直して続ける）。
  useEffect(() => {
    if (!enabled || !check || startedRef.current === check.request.client_job_id) return;
    const current = check;
    startedRef.current = current.request.client_job_id;
    setCheckError(null);
    setCreating(true);
    const load = current.sessionId
      ? getQuerySession(current.sessionId)
      : createQuerySession(chatRequirementSessionRequest(current.request), {
          idempotencyKey: chatRequirementIdempotencyKey(current.request.client_job_id),
        });
    void load
      .then(async (loaded) => {
        const id = current.request.client_job_id;
        if (abandonedRef.current.has(id) || checkRef.current?.request.client_job_id !== id) {
          if (isSessionOpen(loaded)) void cancelQuerySession(loaded.id).catch(() => undefined);
          return;
        }
        if (!current.sessionId) setCheck({ ...current, sessionId: loaded.id });
        setCreating(false);
        await proceed(current, loaded);
      })
      .catch((cause: unknown) => {
        if (checkRef.current?.request.client_job_id !== current.request.client_job_id) return;
        setCreating(false);
        // OCI Enterprise AI が無い環境では要件の検査を使えないので、確認せずに SQL を作る。
        if (!current.sessionId && isRequirementCheckUnavailable(cause)) {
          onSubmitRef.current(current, null);
          return;
        }
        setCheckError(cause);
      });
  }, [check, enabled, proceed, setCheck]);

  // 別の送信に移ったら、前の送信の確認の状態を外す（render 中に前の値と比べて直す）。
  const [shownJobId, setShownJobId] = useState(clientJobId);
  if (shownJobId !== clientJobId) {
    setShownJobId(clientJobId);
    setSession(null);
    setError("");
    controller.setCorrectingItemId(null);
    setCheckError(null);
  }

  const restart = () => {
    if (!check) return;
    // 新しい session で確かめ直す（ジョブはまだ作っていないので、job ID と作成の key を新しくする）。
    const next: ChatRequirementCheck = {
      ...check,
      request: { ...check.request, client_job_id: randomUuid() },
      sessionId: "",
    };
    setCheck(next);
  };

  const skip = () => {
    if (!check) return;
    onSubmit(check, null);
  };

  const cancel = () => {
    if (!check) return;
    if (creating || directing) {
      abandonedRef.current.add(check.request.client_job_id);
      setCreating(false);
      onCancelled(check);
      return;
    }
    void controller.cancelSession().then((done) => {
      if (done) onCancelled(check);
    });
  };

  const discard = () => {
    if (!check) return;
    abandonedRef.current.add(check.request.client_job_id);
    if (isSessionOpen(session)) void cancelQuerySession(session.id).catch(() => undefined);
    setCreating(false);
    setCheck(null);
  };

  const generate = () => {
    if (!check) return;
    const current = check;
    void controller.applyRequirement().then((applied) => {
      if (applied && checkRef.current?.request.client_job_id === current.request.client_job_id) {
        onSubmit(current, applied.id, applied.requirement?.clarified_question_ja);
      }
    });
  };

  const step = session ? chatRequirementStep(session) : null;
  let phase: ChatRequirementPhase;
  if (!check) phase = "idle";
  else if (creating || directing) phase = "checking";
  else if (!session) phase = checkError ? "failed" : "checking";
  else if (step === "ended") phase = "ended";
  else if (step === "ask") phase = "ask";
  else phase = "summary";

  return {
    check,
    controller,
    phase,
    checkError,
    running: phase === "checking",
    retry: restart,
    skip,
    cancel,
    generate,
    discard,
  };
}

/** チャットの会話の中の AI要件確認の 1 往復（送った質問の吹き出しと、その下の検査・確認・まとめ）。 */
export function ChatRequirementTurn({
  flow,
  canExecuteSql,
}: {
  flow: ChatRequirementFlow;
  canExecuteSql: boolean;
}) {
  const { check, controller, phase } = flow;
  if (!check) return null;
  const { state, busyAction, error, session } = controller;
  const badge = state ? requirementPhaseBadge(state) : null;
  const cancelButton = (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      icon={X}
      loading={busyAction === "cancel"}
      disabled={busyAction === "answer" || busyAction === "apply"}
      onClick={flow.cancel}
      data-testid="sql-chat-requirement-cancel"
    >
      {t("nl2sql.clarification.cancel")}
    </Button>
  );
  const generateLabel = canExecuteSql
    ? t("chat.requirement.generate")
    : t("chat.requirement.generateOnly");
  const showGenerate = phase === "summary" && controller.showApply && !controller.activeItem;
  return (
    <ChatTurn question={check.request.question} testId="sql-chat-requirement-turn">
      <ChatAnswer testId="sql-chat-requirement" className="gap-4">
        {phase === "checking" ? (
          <ChatProgress
            key={check.request.client_job_id}
            steps={[
              {
                id: "requirement_check",
                label: t("chat.progress.requirement.running"),
                status: "running",
                startedAt: new Date(check.sentAtMs).toISOString(),
              },
            ]}
            labels={CHAT_PROGRESS_LABELS}
            testId="sql-chat-progress"
          />
        ) : null}
        {phase === "failed" ? (
          <ApiErrorBanner
            error={flow.checkError}
            fallback={t("chat.requirement.failed")}
            nextAction={t("chat.requirement.failed.action")}
            testId="sql-chat-requirement-error"
            action={
              <div className="flex flex-wrap gap-2">
                <Button type="button" variant="secondary" size="sm" icon={RotateCcw} onClick={flow.retry}>
                  {t("chat.requirement.retry")}
                </Button>
                <Button type="button" variant="secondary" size="sm" icon={MessageSquareText} onClick={flow.skip}>
                  {t("chat.requirement.skip")}
                </Button>
                {cancelButton}
              </div>
            }
          />
        ) : null}
        {phase === "ask" || phase === "summary" || phase === "ended" ? (
          <section
            aria-labelledby="sql-chat-requirement-title"
            className="grid min-w-0 gap-3"
            data-testid="sql-chat-requirement-clarification"
            data-phase={phase}
          >
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <Sparkles size={16} className="text-accent-fg" aria-hidden="true" />
                <h3 id="sql-chat-requirement-title" className="text-sm font-semibold text-fg">
                  {t("nl2sql.clarification.title")}
                </h3>
                {badge && phase !== "ended" ? <StatusBadge variant={badge.variant} label={badge.label} /> : null}
                {state && phase !== "ended" ? (
                  <span className="text-xs text-fg-muted" data-testid="nl2sql-clarification-review-round">
                    {t("nl2sql.clarification.reviewRound", {
                      round: state.review_round,
                      max: state.max_review_rounds,
                    })}
                  </span>
                ) : null}
              </div>
              {phase !== "ended" ? (
                <p className="mt-1 text-sm leading-6 text-fg-muted">
                  {state?.message_ja || t("chat.requirement.description")}
                </p>
              ) : null}
            </div>
            <div aria-live="polite" aria-atomic="true" className="sr-only">
              {state?.message_ja}
            </div>
            {error && phase !== "ended" ? <Banner severity="danger">{error}</Banner> : null}
            {phase === "ended" ? (
              <Banner severity="warning">
                {session?.error_message_ja || t("chat.requirement.ended")}
              </Banner>
            ) : null}
            <RequirementAnswerProgress active={busyAction === "answer"} />
            {phase !== "ended" ? (
              <>
                <RequirementStateBanners state={state} unresolvedCount={controller.unresolvedCount} />
                <RequirementQuestionForm
                  controller={controller}
                  size="sm"
                  idPrefix="sql-chat-requirement"
                  applyWithRemainingLabel={t("chat.requirement.generateWithRemaining")}
                  onApplyWithRemaining={flow.generate}
                  trailingActions={cancelButton}
                />
                {state && controller.draft ? (
                  <RequirementItemsList controller={controller} />
                ) : null}
                <RequirementSummary
                  controller={controller}
                  idPrefix="sql-chat-requirement"
                  className="grid gap-3"
                />
              </>
            ) : null}
            {showGenerate ? (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <span className="flex items-center gap-2 text-sm text-success-fg">
                  <CheckCircle2 size={16} aria-hidden="true" />
                  {t("chat.requirement.ready")}
                </span>
                <div className="flex flex-wrap items-center justify-end gap-2">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    icon={Play}
                    loading={busyAction === "apply"}
                    disabled={Boolean(busyAction && busyAction !== "apply")}
                    onClick={flow.generate}
                    data-testid="sql-chat-requirement-generate"
                  >
                    {generateLabel}
                  </Button>
                  {cancelButton}
                </div>
              </div>
            ) : null}
            {phase === "ended" ? (
              <div className="flex flex-wrap justify-end gap-2">
                <Button type="button" variant="secondary" size="sm" icon={RotateCcw} onClick={flow.retry}>
                  {t("chat.requirement.retry")}
                </Button>
                {cancelButton}
              </div>
            ) : null}
            {!showGenerate && phase !== "ended" && !controller.activeItem ? (
              <div className="flex flex-wrap justify-end gap-2">{cancelButton}</div>
            ) : null}
          </section>
        ) : null}
      </ChatAnswer>
    </ChatTurn>
  );
}
