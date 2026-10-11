import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useValuesChanged } from "@/lib/render-sync";
import { CheckCircle2, Pencil, Send, Sparkles, X } from "lucide-react";

import {
  Banner,
  Button,
  Disclosure,
  FieldError,
  ProcessingIndicator,
  StatusBadge,
  TextareaField,
} from "@production-ready/ui";

import { isAbortError } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

import {
  answerQuerySessionClarification,
  applyQuerySessionRequirement,
  cancelQuerySession,
  confirmOntologyProfileRecommendation,
  createQuerySession,
  getQuerySession,
  QuerySessionVersionConflictError,
  recommendOntologyProfiles,
} from "../ontology/api";
import type {
  OntologyProfileRecommendation,
  QuerySession,
  RequirementItem,
  RequirementSection,
  RequirementState,
  RequirementStatus,
} from "../ontology/types";
import type { Nl2SqlEngine } from "../types";

interface GuidedClarificationPanelProps {
  question: string;
  profileId: string;
  engine: Nl2SqlEngine;
  allowedObjects: { table_names: string[]; columns: Record<string, string[]> };
  onClose: () => void;
  /** 確認した要件を反映した（session は閉じた）。質問の文と session の ID を渡す（#1439）。 */
  onApplyQuestion: (question: string, profileId: string, sessionId: string) => void;
}

type ClarificationStartPhase = "recommend_profile" | "confirm_profile" | "prepare_questions";

/** 画面の欄（SELECT / FROM と結合 / WHERE と期間 / GROUP BY / ORDER BY と件数 / WITH / そのほか）。 */
const REQUIREMENT_GROUPS: { id: string; labelKey: I18nKey; sections: RequirementSection[] }[] = [
  { id: "select", labelKey: "nl2sql.clarification.section.select", sections: ["select"] },
  { id: "from", labelKey: "nl2sql.clarification.section.from", sections: ["from", "join"] },
  { id: "where", labelKey: "nl2sql.clarification.section.where", sections: ["where", "period"] },
  { id: "group_by", labelKey: "nl2sql.clarification.section.groupBy", sections: ["group_by"] },
  { id: "order_by", labelKey: "nl2sql.clarification.section.orderBy", sections: ["order_by", "limit"] },
  { id: "subquery", labelKey: "nl2sql.clarification.section.subquery", sections: ["subquery"] },
  { id: "other", labelKey: "nl2sql.clarification.section.other", sections: ["other"] },
];

const SECTION_LABEL_KEY: Record<RequirementSection, I18nKey> = {
  select: "nl2sql.clarification.section.select",
  from: "nl2sql.clarification.section.from",
  join: "nl2sql.clarification.section.from",
  where: "nl2sql.clarification.section.where",
  period: "nl2sql.clarification.section.where",
  group_by: "nl2sql.clarification.section.groupBy",
  order_by: "nl2sql.clarification.section.orderBy",
  limit: "nl2sql.clarification.section.orderBy",
  subquery: "nl2sql.clarification.section.subquery",
  other: "nl2sql.clarification.section.other",
};

const STATUS_VARIANT: Record<RequirementStatus, "success" | "info" | "warning"> = {
  confirmed: "success",
  inferred: "info",
  missing: "warning",
  ambiguous: "warning",
};

function isSessionOpen(session: QuerySession | null): session is QuerySession {
  return Boolean(session && session.status !== "done" && session.status !== "cancelled");
}

function itemStatusLabel(item: RequirementItem): string {
  if (item.answered) return t("nl2sql.clarification.itemStatus.answered");
  return t(`nl2sql.clarification.itemStatus.${item.status}` as I18nKey);
}

function phaseBadge(state: RequirementState): { variant: "success" | "warning" | "danger"; label: string } {
  if (state.status === "unanswerable") {
    return { variant: "danger", label: t("nl2sql.clarification.status.unanswerable") };
  }
  if (state.status === "ready") {
    return { variant: "success", label: t("nl2sql.clarification.status.ready") };
  }
  return {
    variant: "warning",
    label: t("nl2sql.clarification.status.needsAnswer", { count: state.unresolved_items.length }),
  };
}

export function GuidedClarificationPanel({
  question,
  profileId,
  engine,
  allowedObjects,
  onClose,
  onApplyQuestion,
}: GuidedClarificationPanelProps) {
  const [session, setSession] = useState<QuerySession | null>(null);
  const [recommendation, setRecommendation] = useState<OntologyProfileRecommendation | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [freeText, setFreeText] = useState("");
  // 確認済みの一覧から「直す」で選んだ項目（質問の欄の代わりに、この項目の入力を出す）。
  const [correctingItemId, setCorrectingItemId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<"start" | "answer" | "apply" | "cancel" | "">("start");
  const [startPhase, setStartPhase] = useState<ClarificationStartPhase>("recommend_profile");
  const startedRef = useRef(false);
  const startControllerRef = useRef<AbortController | null>(null);
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);
  // 画面を離れた（unmount・再読込）ときに、サーバーの session を中止するための参照。
  const sessionRef = useRef<QuerySession | null>(null);
  // 「確認を中止して閉じる」で中止済み、または要件を反映した（閉じた）session は中止しない。
  const releasedRef = useRef(false);
  const mountedRef = useRef(false);
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  // 中止・期限切れなどで終わった確認は、質問と回答の欄を出さず、理由と「閉じる」だけを出す（#1274）。
  const sessionEnded = session?.status === "cancelled" || session?.status === "error";
  const state = sessionEnded ? null : session?.requirement_state ?? null;
  const draft = sessionEnded ? null : session?.requirement ?? null;
  const items = draft?.items ?? [];
  const correctingItem = correctingItemId
    ? items.find((item) => item.id === correctingItemId) ?? null
    : null;
  const activeItem = correctingItem ?? state?.current_item ?? null;

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
    const controller = new AbortController();
    startControllerRef.current = controller;
    void recommendOntologyProfiles(question, { signal: controller.signal })
      .then(async (result) => {
        if (controller.signal.aborted) return;
        if (profileId.toLowerCase() === "all") {
          setRecommendation(result);
          setSelectedProfileId(result.candidates[0]?.profile_id ?? "");
          if (!result.candidates.length) {
            setError(t("nl2sql.clarification.profileEmpty"));
          }
          return;
        }
        await startSessionRef.current(result, profileId, controller.signal);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted && !isAbortError(cause)) {
          setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
        }
      })
      .finally(() => {
        if (startControllerRef.current === controller) startControllerRef.current = null;
        if (!controller.signal.aborted) setBusyAction("");
      });
  }, [allowedObjects, engine, profileId, question]);

  const confirmRecommendedProfile = async () => {
    if (!recommendation || !selectedProfileId || busyAction) return;
    const controller = new AbortController();
    startControllerRef.current = controller;
    setBusyAction("start");
    setError("");
    try {
      await startSession(recommendation, selectedProfileId, controller.signal);
    } catch (cause) {
      if (!controller.signal.aborted && !isAbortError(cause)) {
        setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
      }
    } finally {
      if (startControllerRef.current === controller) startControllerRef.current = null;
      if (!controller.signal.aborted) setBusyAction("");
    }
  };

  // 質問（または直す項目）が変わったレンダーで回答欄を空にする（effect で setState しない）。
  const activeChanged = useValuesChanged([activeItem?.id, session?.current_intent_version]);
  if (activeChanged) {
    setSelectedCandidateIds([]);
    setFreeText("");
  }
  const activeItemId = activeItem?.id;
  useEffect(() => {
    if (activeItemId) {
      requestAnimationFrame(() => questionHeadingRef.current?.focus({ preventScroll: true }));
    }
  }, [activeItemId]);

  const answerReady = selectedCandidateIds.length > 0 || Boolean(freeText.trim());

  const selectCandidate = (candidateId: string) => {
    setFreeText("");
    if (activeItem?.answer_kind === "multi_select") {
      setSelectedCandidateIds((current) =>
        current.includes(candidateId)
          ? current.filter((item) => item !== candidateId)
          : [...current, candidateId]
      );
      return;
    }
    setSelectedCandidateIds([candidateId]);
  };

  const reloadAfterConflict = async (cause: unknown, current: QuerySession) => {
    if (!(cause instanceof QuerySessionVersionConflictError)) return;
    try {
      setSession(await getQuerySession(current.id));
    } catch {
      if (cause.session) setSession(cause.session);
    }
  };

  const submitAnswer = async () => {
    if (!session || !activeItem || !answerReady || busyAction) return;
    setBusyAction("answer");
    setError("");
    try {
      const updated = await answerQuerySessionClarification(session.id, {
        base_version: session.current_intent_version ?? 1,
        item_id: activeItem.id,
        selected_candidate_ids: selectedCandidateIds,
        free_text: freeText.trim(),
      });
      setSession(updated);
      setCorrectingItemId(null);
    } catch (cause) {
      await reloadAfterConflict(cause, session);
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.answer"));
    } finally {
      setBusyAction("");
    }
  };

  const applyRequirement = async () => {
    if (!session || !state?.can_apply || busyAction) return;
    setBusyAction("apply");
    setError("");
    try {
      const applied = await applyQuerySessionRequirement(session.id, {
        base_version: session.current_intent_version ?? 1,
      });
      const clarifiedQuestion = applied.requirement?.clarified_question_ja?.trim();
      if (!clarifiedQuestion) {
        setError(t("nl2sql.clarification.error.apply"));
        return;
      }
      releasedRef.current = true;
      onApplyQuestion(clarifiedQuestion, applied.profile_id, applied.id);
    } catch (cause) {
      await reloadAfterConflict(cause, session);
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.apply"));
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const closePanel = async () => {
    if (busyAction === "start") {
      startControllerRef.current?.abort();
      onClose();
      return;
    }
    if (busyAction) return;
    if (!isSessionOpen(session)) {
      onClose();
      return;
    }
    setBusyAction("cancel");
    setError("");
    try {
      await cancelQuerySession(session.id);
      releasedRef.current = true;
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.cancel"));
    } finally {
      setBusyAction("");
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

  const badge = state ? phaseBadge(state) : null;
  const unresolvedCount = state?.unresolved_items.length ?? 0;
  const answeredCount = state?.answered_count ?? 0;
  const showApply = Boolean(state?.can_apply) && !correctingItem;
  const applyWithRemaining = showApply && unresolvedCount > 0;
  const showFinalSummary = Boolean(state && (state.status === "ready" || state.can_apply));

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
      {busyAction === "answer" ? (
        // 回答の後に LLM の再検査が走ると数秒以上かかる。スピナーは回答のボタンが担う（messaging.md §3.7）。
        <ProcessingIndicator
          active
          operationKey="guided-clarification-answer"
          label={t("nl2sql.clarification.loading.answer")}
          placement="panel"
          activityIcon="none"
          className="rounded-md border border-border bg-surface-sunken px-3 py-2"
          testId="nl2sql-guided-answer-progress"
        />
      ) : null}

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

      {state?.status === "unanswerable" ? (
        <Banner severity="warning">{state.message_ja}</Banner>
      ) : null}
      {state && state.minimum_missing_ja.length > 0 && state.status !== "unanswerable" ? (
        <Banner severity="warning">{t("nl2sql.clarification.minimumMissing")}</Banner>
      ) : null}
      {state?.review_limit_reached && unresolvedCount > 0 ? (
        <Banner severity="warning">
          {t("nl2sql.clarification.limitReached", {
            max: state.max_review_rounds,
            items: state.unresolved_items.map((item) => item.label_ja).join("、"),
          })}
        </Banner>
      ) : null}

      {activeItem && state ? (
        <fieldset
          className="grid gap-3 rounded-md border border-border bg-surface-sunken p-4"
          data-testid="nl2sql-clarification-question"
          data-item-key={activeItem.key}
        >
          <legend className="sr-only">
            {correctingItem
              ? t("nl2sql.clarification.correctTitle", { label: correctingItem.label_ja })
              : activeItem.question_ja || activeItem.label_ja}
          </legend>
          <div>
            <p className="flex flex-wrap items-center gap-2 text-xs font-medium text-fg-muted">
              <span>
                {correctingItem
                  ? t("nl2sql.clarification.correctTitle", { label: correctingItem.label_ja })
                  : t("nl2sql.clarification.step", {
                      step: answeredCount + 1,
                      remaining: unresolvedCount,
                    })}
              </span>
              <StatusBadge
                icon={false}
                variant="neutral"
                label={t(SECTION_LABEL_KEY[activeItem.section])}
              />
            </p>
            <h4
              ref={questionHeadingRef}
              tabIndex={-1}
              className="mt-1 text-sm font-semibold leading-6 text-fg"
            >
              {correctingItem
                ? correctingItem.question_ja || t("nl2sql.clarification.correctLabel", { label: correctingItem.label_ja })
                : activeItem.question_ja || activeItem.label_ja}
            </h4>
            {correctingItem ? (
              <p className="mt-1 text-xs leading-5 text-fg-muted">
                {t("nl2sql.clarification.correctHint", {
                  value: correctingItem.value_ja || t("nl2sql.clarification.unset"),
                })}
              </p>
            ) : null}
            {activeItem.reason_ja ? (
              <p className="mt-1 text-xs leading-5 text-fg-muted">{activeItem.reason_ja}</p>
            ) : null}
          </div>

          {activeItem.candidates.length > 0 ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {activeItem.candidates.map((candidate) => {
                const checked = selectedCandidateIds.includes(candidate.id);
                return (
                  <label
                    key={candidate.id}
                    className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-border-control bg-surface px-3 py-2 text-sm text-fg has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle"
                  >
                    <input
                      type={activeItem.answer_kind === "multi_select" ? "checkbox" : "radio"}
                      name={`requirement-${activeItem.id}`}
                      value={candidate.id}
                      checked={checked}
                      disabled={Boolean(busyAction)}
                      onChange={() => selectCandidate(candidate.id)}
                      className="mt-1 h-4 w-4 accent-accent-emphasis"
                    />
                    <span className="min-w-0">
                      <span className="block break-words font-medium">{candidate.label_ja}</span>
                      {candidate.description_ja ? (
                        <span className="mt-0.5 block text-xs leading-5 text-fg-muted">
                          {candidate.description_ja}
                        </span>
                      ) : null}
                      {candidate.refs_ja && candidate.refs_ja !== candidate.label_ja ? (
                        <span className="mt-0.5 block break-all font-mono text-xs leading-5 text-fg-muted">
                          {candidate.refs_ja}
                        </span>
                      ) : null}
                      {candidate.evidence_ja ? (
                        <span className="mt-0.5 block text-xs leading-5 text-fg-subtle">
                          {t("nl2sql.clarification.evidence", { evidence: candidate.evidence_ja })}
                        </span>
                      ) : null}
                    </span>
                  </label>
                );
              })}
            </div>
          ) : null}

          <TextareaField
            id="nl2sql-clarification-free-text"
            label={t("nl2sql.clarification.other")}
            value={freeText}
            onChange={(event) => {
              setFreeText(event.currentTarget.value);
              if (event.currentTarget.value) setSelectedCandidateIds([]);
            }}
            disabled={Boolean(busyAction)}
            rows={2}
            placeholder={t("nl2sql.clarification.otherPlaceholder")}
          />
          {!answerReady && error ? (
            <FieldError
              id="nl2sql-clarification-answer-error"
              message={t("nl2sql.clarification.answerRequired")}
            />
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            {correctingItem ? (
              <Button
                type="button"
                variant="ghost"
                size="lg"
                icon={X}
                disabled={Boolean(busyAction)}
                onClick={() => setCorrectingItemId(null)}
              >
                {t("nl2sql.clarification.correctCancel")}
              </Button>
            ) : null}
            {applyWithRemaining ? (
              <Button
                type="button"
                variant="secondary"
                size="lg"
                icon={CheckCircle2}
                loading={busyAction === "apply"}
                disabled={Boolean(busyAction && busyAction !== "apply")}
                onClick={() => void applyRequirement()}
              >
                {t("nl2sql.clarification.applyWithRemaining")}
              </Button>
            ) : null}
            <Button
              icon={Send}
              type="button"
              variant="primary"
              size="lg"
              loading={busyAction === "answer"}
              disabled={!answerReady || Boolean(busyAction && busyAction !== "answer")}
              onClick={() => void submitAnswer()}
            >
              {correctingItem
                ? t("nl2sql.clarification.correctSubmit")
                : t("nl2sql.clarification.answer")}
            </Button>
            {correctingItem ? null : closeButton}
          </div>
        </fieldset>
      ) : null}

      {state && draft ? (
        <section
          aria-labelledby="nl2sql-requirements-title"
          className="grid gap-3"
          data-testid="nl2sql-clarification-requirements"
        >
          <div>
            <h4 id="nl2sql-requirements-title" className="text-sm font-semibold text-fg">
              {t("nl2sql.clarification.requirements")}
            </h4>
            <p className="mt-0.5 text-xs leading-5 text-fg-muted">
              {t("nl2sql.clarification.requirementsHint")}
            </p>
          </div>
          <div className="grid gap-2 lg:grid-cols-2">
            {REQUIREMENT_GROUPS.map((group) => {
              const groupItems = items.filter((item) => group.sections.includes(item.section));
              return (
                <section
                  key={group.id}
                  aria-labelledby={`nl2sql-requirement-group-${group.id}`}
                  className="min-w-0 rounded-md border border-border bg-surface-sunken p-3"
                  data-testid={`nl2sql-requirement-group-${group.id}`}
                >
                  <h5
                    id={`nl2sql-requirement-group-${group.id}`}
                    className="text-xs font-semibold text-fg-muted"
                  >
                    {t(group.labelKey)}
                  </h5>
                  {groupItems.length === 0 ? (
                    <p className="mt-1 text-sm text-fg-subtle">{t("nl2sql.clarification.sectionEmpty")}</p>
                  ) : (
                    <ul className="mt-1 grid gap-2">
                      {groupItems.map((item) => {
                        const isCurrent = item.id === activeItem?.id;
                        const unresolved = !item.answered && (item.status === "missing" || item.status === "ambiguous");
                        return (
                          <li
                            key={item.id}
                            className="grid gap-1 border-t border-border pt-2 first:border-t-0 first:pt-0"
                            data-testid="nl2sql-requirement-item"
                            data-item-key={item.key}
                            data-item-status={item.answered ? "answered" : item.status}
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <span className="text-xs text-fg-muted">{item.label_ja}</span>
                              <span className="flex items-center gap-1">
                                <StatusBadge
                                  variant={item.answered ? "success" : STATUS_VARIANT[item.status]}
                                  label={itemStatusLabel(item)}
                                />
                                {!unresolved && !isCurrent && state.status !== "unanswerable" ? (
                                  <Button
                                    type="button"
                                    variant="ghost"
                                    size="sm"
                                    icon={Pencil}
                                    disabled={Boolean(busyAction)}
                                    aria-label={t("nl2sql.clarification.correctLabel", { label: item.label_ja })}
                                    onClick={() => setCorrectingItemId(item.id)}
                                  >
                                    {t("nl2sql.clarification.correct")}
                                  </Button>
                                ) : null}
                              </span>
                            </div>
                            <p className="break-words text-sm font-medium leading-6 text-fg">
                              {item.value_ja || t("nl2sql.clarification.unset")}
                            </p>
                            {item.refs_ja && !item.value_ja.includes(item.refs_ja) ? (
                              <p className="break-all font-mono text-xs leading-5 text-fg-muted">{item.refs_ja}</p>
                            ) : null}
                            {item.period?.range_ja && !item.value_ja.includes(item.period.range_ja) ? (
                              <p className="text-xs leading-5 text-fg-muted">{item.period.range_ja}</p>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </section>
              );
            })}
          </div>
          {draft.warnings_ja.length > 0 ? (
            <Disclosure summary={t("nl2sql.clarification.warnings")} surface="sunken">
              <ul className="list-disc space-y-1 pl-5 text-xs leading-5 text-fg-muted">
                {draft.warnings_ja.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            </Disclosure>
          ) : null}
        </section>
      ) : null}

      {showFinalSummary && state && draft ? (
        <section
          aria-labelledby="nl2sql-requirement-summary-title"
          className="grid gap-3 border-t border-border pt-4"
          data-testid="nl2sql-clarification-summary"
        >
          <h4 id="nl2sql-requirement-summary-title" className="text-sm font-semibold text-fg">
            {t("nl2sql.clarification.summaryTitle")}
          </h4>
          <div className="grid gap-1">
            <p className="text-xs font-medium text-fg-muted">
              {t("nl2sql.clarification.clarifiedQuestion")}
            </p>
            <p
              className="break-words rounded-md border border-border bg-surface-sunken px-3 py-2 text-sm leading-6 text-fg"
              data-testid="nl2sql-clarified-question"
            >
              {draft.clarified_question_ja}
            </p>
          </div>
          <div className="grid gap-1">
            <p className="text-xs font-medium text-fg-muted">{t("nl2sql.clarification.skeleton")}</p>
            <pre
              data-surface="code"
              tabIndex={0}
              role="region"
              aria-label={t("nl2sql.clarification.skeleton")}
              className="max-h-80 overflow-auto rounded-md border border-border p-3 font-mono text-xs leading-5"
              data-testid="nl2sql-clarification-skeleton"
            >
              {state.sql_skeleton}
            </pre>
            <p className="text-xs leading-5 text-fg-muted">{t("nl2sql.clarification.skeletonNote")}</p>
          </div>
        </section>
      ) : null}

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

      {!activeItem && !(showApply) && !(recommendation && !session) ? (
        <div className="flex flex-wrap justify-end border-t border-border pt-4">{closeButton}</div>
      ) : null}
    </section>
  );
}
