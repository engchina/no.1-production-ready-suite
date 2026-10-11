import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CheckCircle2, Sparkles, X } from "lucide-react";

import { Banner, Button, ProcessingIndicator, StatusBadge } from "@production-ready/ui";

import { isAbortError } from "@/lib/api";
import { t } from "@/lib/i18n";

import {
  cancelQuerySession,
  confirmOntologyProfileRecommendation,
  createQuerySession,
  recommendOntologyProfiles,
} from "../ontology/api";
import type { OntologyProfileRecommendation, QuerySession } from "../ontology/types";
import type { Nl2SqlEngine } from "../types";
import {
  isSessionOpen,
  RequirementAnswerProgress,
  RequirementItemsList,
  RequirementQuestionForm,
  requirementPhaseBadge,
  RequirementStateBanners,
  RequirementSummary,
  useRequirementSession,
} from "./RequirementClarification";

interface GuidedClarificationPanelProps {
  question: string;
  profileId: string;
  engine: Nl2SqlEngine;
  allowedObjects: { table_names: string[]; columns: Record<string, string[]> };
  onClose: () => void;
  /**
   * 確認した要件を反映した（session は閉じた）。質問の文と session の ID を渡す（#1439）。
   * 画面に反映できなかった（草稿を保存できない）ときは false を返し、パネルを残す。
   */
  onApplyQuestion: (question: string, profileId: string, sessionId: string) => boolean;
}

type ClarificationStartPhase = "recommend_profile" | "confirm_profile" | "prepare_questions";

/**
 * SQL 生成の画面の AI要件確認（#1439）。業務プロファイルの推薦・確定の後に session を作り、1 問ずつの回答・直し・
 * 要件のまとめ・反映を出す。回答・直し・反映・中止と、質問・一覧・まとめの部品はチャットと共通（#1442）。
 */
export function GuidedClarificationPanel({
  question,
  profileId,
  engine,
  allowedObjects,
  onClose,
  onApplyQuestion,
}: GuidedClarificationPanelProps) {
  const controller = useRequirementSession("start");
  const { session, setSession, busyAction, setBusyAction, error, setError, sessionEnded, state, draft } =
    controller;
  const activeItem = controller.activeItem;
  const [recommendation, setRecommendation] = useState<OntologyProfileRecommendation | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [startPhase, setStartPhase] = useState<ClarificationStartPhase>("recommend_profile");
  const startedRef = useRef(false);
  const startControllerRef = useRef<AbortController | null>(null);
  // 画面を離れた（unmount・再読込）ときに、サーバーの session を中止するための参照。
  const sessionRef = useRef<QuerySession | null>(null);
  // 「確認を中止して閉じる」で中止済み、または要件を反映した（閉じた）session は中止しない。
  const releasedRef = useRef(false);
  const mountedRef = useRef(false);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  const startSession = async (
    currentRecommendation: OntologyProfileRecommendation,
    targetProfileId: string,
    signal: AbortSignal
  ) => {
    const candidate = currentRecommendation.candidates.find(
      (item) => item.profile_id === targetProfileId
    );
    const revisionId = candidate?.ontology_revision_id || currentRecommendation.ontology_revision_id;
    setStartPhase("confirm_profile");
    const { confirmation_token } = await confirmOntologyProfileRecommendation(
      currentRecommendation.id,
      targetProfileId,
      revisionId,
      { signal }
    );
    setStartPhase("prepare_questions");
    // 作成の request は中断しない。送った後に中断すると、サーバーに作られた session の ID が
    // 分からず中止できないため、応答を待ってから中止する。
    const created = await createQuerySession({
      question,
      profile_id: targetProfileId,
      allowed_objects: allowedObjects,
      engine,
      profile_confirmation_token: confirmation_token,
      clarification_mode: "guided",
    });
    if (signal.aborted) {
      void cancelQuerySession(created.id).catch(() => undefined);
      return;
    }
    sessionRef.current = created;
    setSession(created);
    setRecommendation(null);
    setError("");
  };

  // パネルを閉じずに画面を離れた（別のページへ移動・再読込・タブを閉じる）ときは、
  // 開始の処理を止め、進行中の session をサーバーで中止する。
  useEffect(() => {
    mountedRef.current = true;
    const handlePageHide = (event: PageTransitionEvent) => {
      // bfcache に入るだけなら戻ってきたときに続けられるので中止しない。
      if (event.persisted) return;
      const current = sessionRef.current;
      if (releasedRef.current || !isSessionOpen(current)) return;
      releasedRef.current = true;
      void cancelQuerySession(current.id, { keepalive: true }).catch(() => undefined);
    };
    window.addEventListener("pagehide", handlePageHide);
    return () => {
      mountedRef.current = false;
      window.removeEventListener("pagehide", handlePageHide);
      // StrictMode の検証用の unmount → 再 mount では中止しない（再 mount で true に戻る）。
      window.setTimeout(() => {
        if (mountedRef.current) return;
        startControllerRef.current?.abort();
        const current = sessionRef.current;
        if (releasedRef.current || !isSessionOpen(current)) return;
        releasedRef.current = true;
        void cancelQuerySession(current.id).catch(() => undefined);
      }, 0);
    };
  }, []);

  // 開始は 1 回だけ。最新の startSession を commit 時に ref へ入れて呼ぶ（startSession は毎レンダーで作り直される）。
  const startSessionRef = useRef(startSession);
  useLayoutEffect(() => {
    startSessionRef.current = startSession;
  });
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const abort = new AbortController();
    startControllerRef.current = abort;
    void recommendOntologyProfiles(question, { signal: abort.signal })
      .then(async (result) => {
        if (abort.signal.aborted) return;
        if (profileId.toLowerCase() === "all") {
          setRecommendation(result);
          setSelectedProfileId(result.candidates[0]?.profile_id ?? "");
          if (!result.candidates.length) {
            setError(t("nl2sql.clarification.profileEmpty"));
          }
          return;
        }
        await startSessionRef.current(result, profileId, abort.signal);
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted && !isAbortError(cause)) {
          setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
        }
      })
      .finally(() => {
        if (startControllerRef.current === abort) startControllerRef.current = null;
        if (!abort.signal.aborted) setBusyAction("");
      });
  }, [allowedObjects, engine, profileId, question, setBusyAction, setError]);

  const confirmRecommendedProfile = async () => {
    if (!recommendation || !selectedProfileId || busyAction) return;
    const abort = new AbortController();
    startControllerRef.current = abort;
    setBusyAction("start");
    setError("");
    try {
      await startSession(recommendation, selectedProfileId, abort.signal);
    } catch (cause) {
      if (!abort.signal.aborted && !isAbortError(cause)) {
        setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
      }
    } finally {
      if (startControllerRef.current === abort) startControllerRef.current = null;
      if (!abort.signal.aborted) setBusyAction("");
    }
  };

  const applyRequirement = async () => {
    const applied = await controller.applyRequirement();
    if (!applied) return;
    releasedRef.current = true;
    onApplyQuestion(
      applied.requirement?.clarified_question_ja?.trim() ?? "",
      applied.profile_id,
      applied.id
    );
  };

  const closePanel = async () => {
    if (busyAction === "start") {
      startControllerRef.current?.abort();
      onClose();
      return;
    }
    if (busyAction) return;
    if (await controller.cancelSession()) {
      releasedRef.current = true;
      onClose();
    }
  };

  const closeButton = (
    <Button
      type="button"
      variant="ghost"
      size="lg"
      loading={busyAction === "cancel"}
      disabled={busyAction === "answer" || busyAction === "apply"}
      onClick={() => void closePanel()}
      icon={X}
    >
      {t("nl2sql.clarification.close")}
    </Button>
  );

  const badge = state ? requirementPhaseBadge(state) : null;
  const showApply = controller.showApply;

  return (
    <section
      aria-labelledby="nl2sql-guided-clarification-title"
      className="grid gap-4 rounded-md border border-accent-emphasis bg-surface p-4"
      data-testid="nl2sql-guided-clarification"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Sparkles size={20} className="text-accent-fg" aria-hidden="true" />
          <h3 id="nl2sql-guided-clarification-title" className="text-base font-semibold text-fg">
            {t("nl2sql.clarification.title")}
          </h3>
          {badge ? <StatusBadge variant={badge.variant} label={badge.label} /> : null}
          {state ? (
            <span className="text-xs text-fg-muted" data-testid="nl2sql-clarification-review-round">
              {t("nl2sql.clarification.reviewRound", {
                round: state.review_round,
                max: state.max_review_rounds,
              })}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-sm leading-6 text-fg-muted">
          {state?.message_ja || t("nl2sql.clarification.description")}
        </p>
      </div>

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {state?.message_ja}
      </div>

      {error && !sessionEnded ? <Banner severity="danger">{error}</Banner> : null}
      {sessionEnded ? (
        <Banner severity="warning">
          {session?.error_message_ja || t("nl2sql.clarification.ended")}
        </Banner>
      ) : null}

      {busyAction === "start" ? (
        <div
          className="rounded-md border border-border bg-surface-sunken p-4"
          data-start-phase={startPhase}
        >
          <ProcessingIndicator
            active
            operationKey="guided-clarification-start"
            label={
              startPhase === "recommend_profile"
                ? t("nl2sql.clarification.loading.recommendProfile")
                : startPhase === "confirm_profile"
                  ? t("nl2sql.clarification.loading.confirmProfile")
                  : t("nl2sql.clarification.loading.prepareQuestions")
            }
            placement="panel"
            testId="nl2sql-guided-start-progress"
          />
        </div>
      ) : null}
      <RequirementAnswerProgress active={busyAction === "answer"} />

      {recommendation && !session ? (
        <fieldset className="grid gap-3 rounded-md border border-border bg-surface-sunken p-4">
          <legend className="px-1 text-sm font-semibold text-fg">
            {t("nl2sql.clarification.profileQuestion")}
          </legend>
          <p className="text-xs leading-5 text-fg-muted">
            {t("nl2sql.clarification.profileReason")}
          </p>
          <div className="grid gap-2">
            {recommendation.candidates.slice(0, 3).map((candidate, index) => (
              <label
                key={`${candidate.profile_id}:${candidate.ontology_revision_id}`}
                className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-border-control bg-surface px-3 py-2 text-sm has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
              >
                <input
                  type="radio"
                  name="guided-profile"
                  checked={selectedProfileId === candidate.profile_id}
                  onChange={() => setSelectedProfileId(candidate.profile_id)}
                  className="mt-1 h-4 w-4 accent-accent-emphasis"
                />
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 font-medium text-fg">
                    {candidate.profile_name}
                    {index === 0 ? <StatusBadge icon={false} variant="info" label={t("nl2sql.clarification.recommended")} /> : null}
                  </span>
                  {candidate.reasons_ja.length ? (
                    <span className="mt-0.5 block text-xs leading-5 text-fg-muted">
                      {candidate.reasons_ja.join(" ")}
                    </span>
                  ) : null}
                </span>
              </label>
            ))}
          </div>
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="lg"
              disabled={!selectedProfileId || Boolean(busyAction)}
              icon={CheckCircle2}
              onClick={() => void confirmRecommendedProfile()}
            >
              {t("nl2sql.clarification.confirmProfile")}
            </Button>
            {closeButton}
          </div>
        </fieldset>
      ) : null}

      <RequirementStateBanners state={state} unresolvedCount={controller.unresolvedCount} />

      <RequirementQuestionForm
        controller={controller}
        onApplyWithRemaining={() => void applyRequirement()}
        trailingActions={closeButton}
      />

      {state && draft ? <RequirementItemsList controller={controller} /> : null}

      <RequirementSummary controller={controller} />

      {showApply && !activeItem ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <span className="flex items-center gap-2 text-sm text-success-fg">
            <CheckCircle2 size={16} aria-hidden="true" />
            {t("nl2sql.clarification.ready")}
          </span>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="lg"
              loading={busyAction === "apply"}
              disabled={Boolean(busyAction && busyAction !== "apply")}
              onClick={() => void applyRequirement()}
              icon={Sparkles}
            >
              {t("nl2sql.clarification.apply")}
            </Button>
            {closeButton}
          </div>
        </div>
      ) : null}

      {!activeItem && !showApply && !(recommendation && !session) ? (
        <div className="flex flex-wrap justify-end border-t border-border pt-4">{closeButton}</div>
      ) : null}
    </section>
  );
}
