import { useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, Sparkles, X } from "lucide-react";

import { Banner } from "@engchina/production-ready-ui";

import { ProcessingIndicator } from "@/components/ProcessingState";
import { Button } from "@/components/ui/button";
import { FieldError } from "@/components/ui/field-error";
import { StatusBadge } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";

import {
  answerQuerySessionClarification,
  cancelQuerySession,
  confirmOntologyProfileRecommendation,
  createQuerySession,
  getQuerySession,
  QuerySessionVersionConflictError,
  recommendOntologyProfiles,
} from "../ontology/api";
import type {
  ClarificationEvidenceSource,
  ClarificationQuestion,
  OntologyProfileRecommendation,
  QuerySession,
} from "../ontology/types";
import type { Nl2SqlEngine } from "../types";

interface GuidedClarificationPanelProps {
  question: string;
  profileId: string;
  engine: Nl2SqlEngine;
  allowedObjects: { table_names: string[]; columns: Record<string, string[]> };
  onClose: () => void;
  onApplyQuestion: (question: string) => void;
}

const SOURCE_LABELS: Record<ClarificationEvidenceSource, string> = {
  user: "nl2sql.clarification.source.user",
  profile: "nl2sql.clarification.source.profile",
  ontology: "nl2sql.clarification.source.ontology",
  schema: "nl2sql.clarification.source.schema",
  default: "nl2sql.clarification.source.default",
};

interface ManualAnswerValue {
  optionIds: string[];
  freeText: string;
}

type ClarificationStartPhase = "recommend_profile" | "confirm_profile" | "prepare_questions";

function TechnicalEvidence({ name, evidence }: { name: string; evidence: string }) {
  const [open, setOpen] = useState(false);
  const contentId = useId();
  return (
    <div className="text-xs text-muted">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label={t("nl2sql.clarification.evidenceFor", { name })}
        aria-expanded={open}
        aria-controls={contentId}
        className="-ml-2 min-h-11 px-2 text-xs font-normal text-muted"
        onClick={() => setOpen((current) => !current)}
      >
        {t("nl2sql.clarification.evidence")}
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={open ? "rotate-180" : ""}
        />
      </Button>
      {open ? (
        <div id={contentId} className="pb-2">
          <span className="mb-1 block text-[11px] font-medium uppercase tracking-wide text-muted">
            {t("nl2sql.clarification.evidenceAudience")}
          </span>
          <span className="block break-all font-mono">{evidence}</span>
        </div>
      ) : null}
    </div>
  );
}

function latestIntent(session: QuerySession | null) {
  if (!session?.intents?.length) return null;
  const version = session.current_intent_version ?? 1;
  return [...session.intents].reverse().find((item) => item.version === version) ?? null;
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
  const [selectedOptionIds, setSelectedOptionIds] = useState<string[]>([]);
  const [freeText, setFreeText] = useState("");
  const [manualAnswers, setManualAnswers] = useState<Record<string, ManualAnswerValue>>({});
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<"start" | "answer" | "cancel" | "">("start");
  const [startPhase, setStartPhase] = useState<ClarificationStartPhase>("recommend_profile");
  const startedRef = useRef(false);
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);

  const clarification = session?.clarification ?? null;
  const currentQuestion = clarification?.current_question ?? null;
  const intent = latestIntent(session);

  const startSession = async (
    currentRecommendation: OntologyProfileRecommendation,
    targetProfileId: string
  ) => {
    const candidate = currentRecommendation.candidates.find(
      (item) => item.profile_id === targetProfileId
    );
    const revisionId = candidate?.ontology_revision_id || currentRecommendation.ontology_revision_id;
    setStartPhase("confirm_profile");
    const { confirmation_token } = await confirmOntologyProfileRecommendation(
      currentRecommendation.id,
      targetProfileId,
      revisionId
    );
    setStartPhase("prepare_questions");
    const created = await createQuerySession({
      question,
      profile_id: targetProfileId,
      allowed_objects: allowedObjects,
      engine,
      profile_confirmation_token: confirmation_token,
      clarification_mode: "guided",
    });
    setSession(created);
    setRecommendation(null);
    setError("");
  };

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void recommendOntologyProfiles(question)
      .then(async (result) => {
        if (profileId.toLowerCase() === "all") {
          setRecommendation(result);
          setSelectedProfileId(result.candidates[0]?.profile_id ?? "");
          if (!result.candidates.length) {
            setError(t("nl2sql.clarification.profileEmpty"));
          }
          return;
        }
        await startSession(result, profileId);
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
      })
      .finally(() => setBusyAction(""));
  }, [allowedObjects, engine, profileId, question]);

  const confirmRecommendedProfile = async () => {
    if (!recommendation || !selectedProfileId || busyAction) return;
    setBusyAction("start");
    setError("");
    try {
      await startSession(recommendation, selectedProfileId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.start"));
    } finally {
      setBusyAction("");
    }
  };

  useEffect(() => {
    setSelectedOptionIds([]);
    setFreeText("");
    if (currentQuestion) {
      requestAnimationFrame(() => questionHeadingRef.current?.focus({ preventScroll: true }));
    }
  }, [currentQuestion?.id]);

  const selectedCount = selectedOptionIds.length;
  const answerReady = selectedCount > 0 || Boolean(freeText.trim());
  const completenessLabel = useMemo(() => {
    if (!clarification) return "";
    if (clarification.can_generate_sql) return t("nl2sql.clarification.complete");
    return t("nl2sql.clarification.completeness", {
      confirmed: clarification.required_confirmed,
      total: clarification.required_total,
    });
  }, [clarification]);

  const selectOption = (optionId: string) => {
    setFreeText("");
    if (currentQuestion?.answer_kind === "multi_select") {
      setSelectedOptionIds((current) =>
        current.includes(optionId)
          ? current.filter((item) => item !== optionId)
          : [...current, optionId]
      );
      return;
    }
    setSelectedOptionIds([optionId]);
  };

  const updateManualAnswer = (
    question: ClarificationQuestion,
    update: Partial<ManualAnswerValue>
  ) => {
    setManualAnswers((current) => {
      const previous = current[question.id] ?? { optionIds: [], freeText: "" };
      const next = { ...previous, ...update };
      if (update.optionIds?.length) next.freeText = "";
      if (update.freeText) next.optionIds = [];
      return { ...current, [question.id]: next };
    });
  };

  const manualQuestions = clarification?.remaining_questions ?? [];
  const manualAnswersReady = manualQuestions.length > 0 && manualQuestions.every((question) => {
    const answer = manualAnswers[question.id];
    return Boolean(answer?.optionIds.length || answer?.freeText.trim());
  });

  const submitManualAnswers = async () => {
    if (!session || !manualAnswersReady || busyAction) return;
    setBusyAction("answer");
    setError("");
    try {
      let updated = session;
      // 画面上は一括フォームだが、公開 API の version / current-question 契約を維持して
      // 1 件ずつ順序通りに確定する。
      for (let index = 0; index < manualQuestions.length; index += 1) {
        const questionToAnswer = updated.clarification?.current_question;
        if (!questionToAnswer) break;
        const value = manualAnswers[questionToAnswer.id];
        if (!value) throw new Error(t("nl2sql.clarification.answerRequired"));
        updated = await answerQuerySessionClarification(updated.id, {
          base_version: updated.current_intent_version ?? 1,
          question_id: questionToAnswer.id,
          selected_option_ids: value.optionIds,
          free_text: value.freeText.trim(),
        });
      }
      setSession(updated);
      setManualAnswers({});
    } catch (cause) {
      if (cause instanceof QuerySessionVersionConflictError) {
        try {
          setSession(await getQuerySession(session.id));
        } catch {
          if (cause.session) setSession(cause.session);
        }
      }
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.answer"));
    } finally {
      setBusyAction("");
    }
  };

  const answerCurrentQuestion = async () => {
    if (!session || !currentQuestion || !answerReady || busyAction) return;
    setBusyAction("answer");
    setError("");
    try {
      const updated = await answerQuerySessionClarification(session.id, {
        base_version: session.current_intent_version ?? 1,
        question_id: currentQuestion.id,
        selected_option_ids: selectedOptionIds,
        free_text: freeText.trim(),
      });
      setSession(updated);
    } catch (cause) {
      if (cause instanceof QuerySessionVersionConflictError) {
        try {
          setSession(await getQuerySession(session.id));
        } catch {
          if (cause.session) setSession(cause.session);
        }
      }
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.answer"));
    } finally {
      setBusyAction("");
    }
  };

  const applyQuestion = () => {
    if (!clarification?.can_generate_sql || busyAction) return;
    const clarifiedQuestion = intent?.question_effective?.trim();
    if (!clarifiedQuestion) {
      setError(t("nl2sql.clarification.error.apply"));
      return;
    }
    setError("");
    onApplyQuestion(clarifiedQuestion);
  };

  const closePanel = async () => {
    if (busyAction) return;
    if (!session || session.status === "done" || session.status === "cancelled") {
      onClose();
      return;
    }
    setBusyAction("cancel");
    setError("");
    try {
      await cancelQuerySession(session.id);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.cancel"));
    } finally {
      setBusyAction("");
    }
  };

  const renderCloseButton = (size: "md" | "lg" = "md") => (
    <Button
      type="button"
      variant="ghost"
      size={size}
      className="min-h-11"
      loading={busyAction === "cancel"}
      disabled={Boolean(busyAction && busyAction !== "cancel")}
      onClick={() => void closePanel()}
    >
      <X size={16} aria-hidden="true" />
      {t("nl2sql.clarification.close")}
    </Button>
  );

  const hasStepAction = Boolean(
    (recommendation && !session)
      || (currentQuestion && !clarification?.manual_completion_required)
      || clarification?.manual_completion_required
      || clarification?.can_generate_sql
  );

  return (
    <section
      aria-labelledby="nl2sql-guided-clarification-title"
      className="grid gap-4 rounded-md border border-primary/30 bg-card p-4"
      data-testid="nl2sql-guided-clarification"
    >
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Sparkles size={18} className="text-primary" aria-hidden="true" />
            <h3 id="nl2sql-guided-clarification-title" className="text-base font-semibold text-foreground">
              {t("nl2sql.clarification.title")}
            </h3>
            {clarification ? (
              <StatusBadge
                variant={clarification.can_generate_sql ? "success" : "pending"}
                label={completenessLabel}
              />
            ) : null}
          </div>
          <p className="mt-1 text-sm leading-6 text-muted">
            {clarification?.message_ja || t("nl2sql.clarification.description")}
          </p>
        </div>
      </div>

      <div aria-live="polite" aria-atomic="true" className="sr-only">
        {clarification?.message_ja}
      </div>

      {error ? <Banner severity="danger">{error}</Banner> : null}

      {busyAction === "start" ? (
        <div
          className="rounded-md border border-border bg-background p-4"
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

      {recommendation && !session ? (
        <fieldset className="grid gap-3 rounded-md border border-border bg-background p-4">
          <legend className="px-1 text-sm font-semibold text-foreground">
            {t("nl2sql.clarification.profileQuestion")}
          </legend>
          <p className="text-xs leading-5 text-muted">
            {t("nl2sql.clarification.profileReason")}
          </p>
          <div className="grid gap-2">
            {recommendation.candidates.slice(0, 3).map((candidate, index) => (
              <label
                key={`${candidate.profile_id}:${candidate.ontology_revision_id}`}
                className="flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-control-border bg-card px-3 py-2 text-sm has-[:checked]:border-primary has-[:checked]:bg-primary/5"
              >
                <input
                  type="radio"
                  name="guided-profile"
                  checked={selectedProfileId === candidate.profile_id}
                  onChange={() => setSelectedProfileId(candidate.profile_id)}
                  className="mt-1 h-4 w-4 accent-primary"
                />
                <span className="min-w-0">
                  <span className="flex flex-wrap items-center gap-2 font-medium text-foreground">
                    {candidate.profile_name}
                    {index === 0 ? <StatusBadge variant="info" label={t("nl2sql.clarification.recommended")} /> : null}
                  </span>
                  {candidate.reasons_ja.length ? (
                    <span className="mt-0.5 block text-xs leading-5 text-muted">
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
              size="md"
              className="min-h-11"
              disabled={!selectedProfileId || Boolean(busyAction)}
              onClick={() => void confirmRecommendedProfile()}
            >
              {t("nl2sql.clarification.confirmProfile")}
            </Button>
            {renderCloseButton()}
          </div>
        </fieldset>
      ) : null}

      {clarification?.status === "unanswerable" ? (
        <Banner severity="warning">{clarification.message_ja}</Banner>
      ) : null}

      {currentQuestion && !clarification?.manual_completion_required ? (
        <fieldset className="grid gap-3 rounded-md border border-border bg-background p-4">
          <legend className="sr-only">{currentQuestion.prompt_ja}</legend>
          <div>
            <p className="text-xs font-medium text-muted">
              {t("nl2sql.clarification.step", {
                step: (clarification?.turn_count ?? 0) + 1,
              })}
            </p>
            <h4
              ref={questionHeadingRef}
              tabIndex={-1}
              className="mt-1 text-sm font-semibold leading-6 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
            >
              {currentQuestion.prompt_ja}
            </h4>
            {currentQuestion.reason_ja ? (
              <p className="mt-1 text-xs leading-5 text-muted">{currentQuestion.reason_ja}</p>
            ) : null}
          </div>

          {currentQuestion.options.length > 0 ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {currentQuestion.options.map((option) => {
                const checked = selectedOptionIds.includes(option.id);
                return (
                  <div
                    key={option.id}
                    className="rounded-md border border-control-border bg-card text-sm text-foreground has-[:checked]:border-primary has-[:checked]:bg-primary/5"
                  >
                    <label className="flex min-h-11 cursor-pointer items-start gap-3 px-3 py-2">
                      <input
                        type={currentQuestion.answer_kind === "multi_select" ? "checkbox" : "radio"}
                        name={`clarification-${currentQuestion.id}`}
                        value={option.id}
                        checked={checked}
                        disabled={Boolean(busyAction)}
                        onChange={() => selectOption(option.id)}
                        className="mt-1 h-4 w-4 accent-primary"
                      />
                      <span className="min-w-0">
                        <span className="block font-medium">{option.label_ja}</span>
                        {option.description_ja ? (
                          <span className="mt-0.5 block text-xs leading-5 text-muted">
                            {option.description_ja}
                          </span>
                        ) : null}
                      </span>
                    </label>
                    {option.evidence_ja ? (
                      <div className="border-t border-border/70 px-3 pl-10">
                        <TechnicalEvidence name={option.label_ja} evidence={option.evidence_ja} />
                      </div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : null}

          {currentQuestion.allow_free_text ? (
            <label className="grid gap-1 text-sm font-medium text-foreground">
              <span>{t("nl2sql.clarification.other")}</span>
              <textarea
                value={freeText}
                onChange={(event) => {
                  setFreeText(event.currentTarget.value);
                  if (event.currentTarget.value) setSelectedOptionIds([]);
                }}
                disabled={Boolean(busyAction)}
                rows={2}
                className="min-h-20 rounded-md border border-control-border bg-card px-3 py-2 text-base leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 sm:text-sm"
                placeholder={t("nl2sql.clarification.otherPlaceholder")}
              />
            </label>
          ) : null}
          {!answerReady && error ? (
            <FieldError
              id="nl2sql-clarification-answer-error"
              message={t("nl2sql.clarification.answerRequired")}
            />
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="md"
              className="min-h-11"
              loading={busyAction === "answer"}
              disabled={!answerReady || Boolean(busyAction && busyAction !== "answer")}
              onClick={() => void answerCurrentQuestion()}
            >
              {t("nl2sql.clarification.answerNext")}
            </Button>
            {renderCloseButton()}
          </div>
        </fieldset>
      ) : null}

      {clarification?.manual_completion_required ? (
        <section
          aria-labelledby="nl2sql-manual-completion-title"
          className="grid gap-4 rounded-md border border-warning/40 bg-warning/5 p-4"
        >
          <div>
            <h4 id="nl2sql-manual-completion-title" className="text-sm font-semibold text-foreground">
              {t("nl2sql.clarification.remainingRequired")}
            </h4>
            <p className="mt-1 text-sm leading-6 text-muted">
              {t("nl2sql.clarification.manualCompletion")}
            </p>
          </div>
          {manualQuestions.map((question) => {
            const value = manualAnswers[question.id] ?? { optionIds: [], freeText: "" };
            return (
              <fieldset key={question.id} className="grid gap-2 rounded-md border border-border bg-card p-3">
                <legend className="px-1 text-sm font-medium leading-6 text-foreground">
                  {question.prompt_ja}
                </legend>
                {question.reason_ja ? (
                  <p className="text-xs leading-5 text-muted">{question.reason_ja}</p>
                ) : null}
                {question.options.map((option) => (
                  <div key={option.id} className="rounded-md border border-control-border bg-background has-[:checked]:border-primary has-[:checked]:bg-primary/5">
                    <label className="flex min-h-11 cursor-pointer items-start gap-3 px-3 py-2 text-sm text-foreground">
                      <input
                        type={question.answer_kind === "multi_select" ? "checkbox" : "radio"}
                        name={`manual-${question.id}`}
                        checked={value.optionIds.includes(option.id)}
                        disabled={Boolean(busyAction)}
                        onChange={() => {
                          const optionIds = question.answer_kind === "multi_select"
                            ? (value.optionIds.includes(option.id)
                                ? value.optionIds.filter((item) => item !== option.id)
                                : [...value.optionIds, option.id])
                            : [option.id];
                          updateManualAnswer(question, { optionIds });
                        }}
                        className="mt-1 h-4 w-4 accent-primary"
                      />
                      <span className="min-w-0">
                        <span className="block font-medium">{option.label_ja}</span>
                        {option.description_ja ? (
                          <span className="mt-0.5 block text-xs leading-5 text-muted">
                            {option.description_ja}
                          </span>
                        ) : null}
                      </span>
                    </label>
                    {option.evidence_ja ? (
                      <div className="border-t border-border/70 px-3 pl-10">
                        <TechnicalEvidence name={option.label_ja} evidence={option.evidence_ja} />
                      </div>
                    ) : null}
                  </div>
                ))}
                {question.allow_free_text ? (
                  <label className="grid gap-1 text-sm text-foreground">
                    <span>{t("nl2sql.clarification.other")}</span>
                    <textarea
                      rows={2}
                      value={value.freeText}
                      disabled={Boolean(busyAction)}
                      onChange={(event) => updateManualAnswer(question, { freeText: event.currentTarget.value })}
                      className="min-h-20 rounded-md border border-control-border bg-background px-3 py-2 text-base leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 sm:text-sm"
                    />
                  </label>
                ) : null}
              </fieldset>
            );
          })}
          <div className="flex flex-wrap justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="md"
              className="min-h-11"
              loading={busyAction === "answer"}
              disabled={!manualAnswersReady || Boolean(busyAction && busyAction !== "answer")}
              onClick={() => void submitManualAnswers()}
            >
              {t("nl2sql.clarification.confirmRemaining")}
            </Button>
            {renderCloseButton()}
          </div>
        </section>
      ) : null}

      {clarification && clarification.intent_summary.length > 0 ? (
        <section aria-labelledby="nl2sql-intent-summary-title" className="grid gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 id="nl2sql-intent-summary-title" className="text-sm font-semibold text-foreground">
              {t("nl2sql.clarification.summary")}
            </h4>
            <span className="text-xs text-muted">{completenessLabel}</span>
          </div>
          <dl className="grid gap-2 sm:grid-cols-2">
            {clarification.intent_summary.map((item) => (
              <div key={item.key} className="rounded-md border border-border bg-background p-3">
                <dt className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
                  <span>{item.label_ja}</span>
                  <StatusBadge
                    variant={item.confirmed ? "success" : "neutral"}
                    label={t(SOURCE_LABELS[item.source] as Parameters<typeof t>[0])}
                  />
                </dt>
                <dd className="mt-1 text-sm font-medium leading-6 text-foreground">{item.value_ja}</dd>
                {item.technical_evidence_ja ? (
                  <TechnicalEvidence name={item.label_ja} evidence={item.technical_evidence_ja} />
                ) : null}
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {clarification && clarification.assumptions.length > 0 ? (
        <div className="rounded-md border border-border bg-background p-3 text-sm">
          <p className="font-medium text-foreground">{t("nl2sql.clarification.assumptions")}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-muted">
            {clarification.assumptions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      ) : null}

      {clarification?.can_generate_sql ? (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
          <span className="flex items-center gap-2 text-sm text-success">
            <CheckCircle2 size={16} aria-hidden="true" />
            {t("nl2sql.clarification.ready")}
          </span>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant="primary"
              size="lg"
              className="min-h-11"
              disabled={Boolean(busyAction)}
              onClick={applyQuestion}
            >
              <Sparkles size={16} aria-hidden="true" />
              {t("nl2sql.clarification.apply")}
            </Button>
            {renderCloseButton("lg")}
          </div>
        </div>
      ) : null}

      {!hasStepAction ? (
        <div className="flex flex-wrap justify-end border-t border-border pt-4">
          {renderCloseButton()}
        </div>
      ) : null}
    </section>
  );
}
