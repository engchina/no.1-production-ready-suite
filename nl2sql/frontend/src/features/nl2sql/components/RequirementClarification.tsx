// AI要件確認の共通の部品（#1439 / #1442）。SQL 生成の画面のパネル（GuidedClarificationPanel）と、チャットの
// 会話の中の確認（ChatRequirementTurn）が同じ部品・同じ API で、1 問ずつの質問・回答・確認済みの一覧からの直し・
// 要件のまとめと SQL の骨組みを出す。session の作り方（業務プロファイルの推薦・会話の履歴）と、反映の後の操作
// （質問の欄に入れる・チャットで SQL を生成する）だけを画面ごとに持つ。
import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { CheckCircle2, Pencil, Send, X } from "lucide-react";

import {
  Banner,
  Button,
  Disclosure,
  FieldError,
  ProcessingIndicator,
  StatusBadge,
  TextareaField,
} from "@production-ready/ui";

import { useValuesChanged } from "@/lib/render-sync";
import { t, type I18nKey } from "@/lib/i18n";

import {
  answerQuerySessionClarification,
  applyQuerySessionRequirement,
  cancelQuerySession,
  getQuerySession,
  QuerySessionVersionConflictError,
} from "../ontology/api";
import type {
  QuerySession,
  RequirementItem,
  RequirementSection,
  RequirementState,
  RequirementStatus,
  SqlRequirementDraft,
} from "../ontology/types";

export type RequirementBusyAction = "start" | "answer" | "apply" | "cancel" | "";
type ButtonSize = "sm" | "md" | "lg";

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

export function isSessionOpen(session: QuerySession | null): session is QuerySession {
  return Boolean(session && session.status !== "done" && session.status !== "cancelled");
}

function itemStatusLabel(item: RequirementItem): string {
  if (item.answered) return t("nl2sql.clarification.itemStatus.answered");
  return t(`nl2sql.clarification.itemStatus.${item.status}` as I18nKey);
}

export function requirementPhaseBadge(state: RequirementState): {
  variant: "success" | "warning" | "danger";
  label: string;
} {
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

/** AI要件確認の session の状態と、回答・直し・反映・中止の操作（SQL 生成の画面とチャットで共通）。 */
export interface RequirementSessionController {
  session: QuerySession | null;
  setSession: (session: QuerySession | null) => void;
  busyAction: RequirementBusyAction;
  setBusyAction: (action: RequirementBusyAction) => void;
  error: string;
  setError: (message: string) => void;
  /** 中止・期限切れなどで終わった（質問と回答の欄を出さない）。 */
  sessionEnded: boolean;
  state: RequirementState | null;
  draft: SqlRequirementDraft | null;
  items: RequirementItem[];
  /** 今の質問（確認済みの一覧から直している項目があればそれ）。 */
  activeItem: RequirementItem | null;
  correctingItem: RequirementItem | null;
  setCorrectingItemId: (id: string | null) => void;
  selectedCandidateIds: string[];
  freeText: string;
  answerReady: boolean;
  selectCandidate: (candidateId: string) => void;
  changeFreeText: (value: string) => void;
  questionHeadingRef: RefObject<HTMLHeadingElement | null>;
  submitAnswer: () => Promise<void>;
  /** 反映して session を閉じる。反映した session（要件の本文を含む）を返す。失敗は null。 */
  applyRequirement: () => Promise<QuerySession | null>;
  /** 開いている session をサーバーで中止する。中止できた（または開いていない）ら true。 */
  cancelSession: () => Promise<boolean>;
  /** 回答・反映・中止のどれかが要件確認を進められる状態か（SELECT / FROM が明確で、直している途中でない）。 */
  showApply: boolean;
  unresolvedCount: number;
  answeredCount: number;
  showFinalSummary: boolean;
}

export function useRequirementSession(
  initialBusy: RequirementBusyAction = ""
): RequirementSessionController {
  const [session, setSession] = useState<QuerySession | null>(null);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<string[]>([]);
  const [freeText, setFreeText] = useState("");
  // 確認済みの一覧から「直す」で選んだ項目（質問の欄の代わりに、この項目の入力を出す）。
  const [correctingItemId, setCorrectingItemId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState<RequirementBusyAction>(initialBusy);
  const questionHeadingRef = useRef<HTMLHeadingElement>(null);
  const mountedRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 中止・期限切れなどで終わった確認は、質問と回答の欄を出さず、理由と「閉じる」だけを出す（#1274）。
  const sessionEnded = session?.status === "cancelled" || session?.status === "error";
  const state = sessionEnded ? null : session?.requirement_state ?? null;
  const draft = sessionEnded ? null : session?.requirement ?? null;
  const items = draft?.items ?? [];
  const correctingItem = correctingItemId
    ? items.find((item) => item.id === correctingItemId) ?? null
    : null;
  const activeItem = correctingItem ?? state?.current_item ?? null;

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

  const changeFreeText = (value: string) => {
    setFreeText(value);
    if (value) setSelectedCandidateIds([]);
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
      if (mountedRef.current) setBusyAction("");
    }
  };

  const applyRequirement = async (): Promise<QuerySession | null> => {
    if (!session || busyAction) return null;
    // 反映済み（session は閉じた）で画面への反映だけが失敗したときは、API を呼び直さない。
    if (!session.requirement_applied_at && !state?.can_apply) return null;
    setBusyAction("apply");
    setError("");
    try {
      const applied = session.requirement_applied_at
        ? session
        : await applyQuerySessionRequirement(session.id, {
            base_version: session.current_intent_version ?? 1,
          });
      if (applied !== session) {
        // 状態の表示は反映前のまま保つ（閉じた session の state は使わない）。
        // 閉じた session は画面を離れても中止しない（status を反映後の値にする）。
        setSession({
          ...session,
          status: applied.status ?? "done",
          requirement_applied_at: applied.requirement_applied_at ?? "applied",
          requirement: applied.requirement ?? session.requirement,
        });
      }
      if (!applied.requirement?.clarified_question_ja?.trim()) {
        setError(t("nl2sql.clarification.error.apply"));
        return null;
      }
      return applied;
    } catch (cause) {
      await reloadAfterConflict(cause, session);
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.apply"));
      return null;
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const cancelSession = async (): Promise<boolean> => {
    if (busyAction) return false;
    if (!isSessionOpen(session)) return true;
    setBusyAction("cancel");
    setError("");
    try {
      await cancelQuerySession(session.id);
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("nl2sql.clarification.error.cancel"));
      return false;
    } finally {
      if (mountedRef.current) setBusyAction("");
    }
  };

  const unresolvedCount = state?.unresolved_items.length ?? 0;
  return {
    session,
    setSession,
    busyAction,
    setBusyAction,
    error,
    setError,
    sessionEnded,
    state,
    draft,
    items,
    activeItem,
    correctingItem,
    setCorrectingItemId,
    selectedCandidateIds,
    freeText,
    answerReady,
    selectCandidate,
    changeFreeText,
    questionHeadingRef,
    submitAnswer,
    applyRequirement,
    cancelSession,
    showApply: Boolean(state?.can_apply) && !correctingItem,
    unresolvedCount,
    answeredCount: state?.answered_count ?? 0,
    showFinalSummary: Boolean(state && (state.status === "ready" || state.can_apply)),
  };
}

/** 答えられない質問・最低の基準（SELECT / FROM）・再検査の上限の知らせ。 */
export function RequirementStateBanners({ state, unresolvedCount }: { state: RequirementState | null; unresolvedCount: number }) {
  if (!state) return null;
  return (
    <>
      {state.status === "unanswerable" ? <Banner severity="warning">{state.message_ja}</Banner> : null}
      {state.minimum_missing_ja.length > 0 && state.status !== "unanswerable" ? (
        <Banner severity="warning">{t("nl2sql.clarification.minimumMissing")}</Banner>
      ) : null}
      {state.review_limit_reached && unresolvedCount > 0 ? (
        <Banner severity="warning">
          {t("nl2sql.clarification.limitReached", {
            max: state.max_review_rounds,
            items: state.unresolved_items.map((item) => item.label_ja).join("、"),
          })}
        </Banner>
      ) : null}
    </>
  );
}

/** 回答を反映している間の経過（回答の後に LLM の再検査が走ると数秒以上かかる）。 */
export function RequirementAnswerProgress({ active }: { active: boolean }) {
  if (!active) return null;
  // スピナーは回答のボタンが担う（messaging.md §3.7）。
  return (
    <ProcessingIndicator
      active
      operationKey="guided-clarification-answer"
      label={t("nl2sql.clarification.loading.answer")}
      placement="panel"
      activityIcon="none"
      className="rounded-md border border-border bg-surface-sunken px-3 py-2"
      testId="nl2sql-guided-answer-progress"
    />
  );
}

/**
 * 1 ページに 1 問の質問の欄（選択肢・複数選択・自由入力）。確認済みの一覧から直している項目があれば、その項目の
 * 入力を出す。`trailingActions` は回答のボタンの右に置く操作（中止など）。
 */
export function RequirementQuestionForm({
  controller,
  size = "lg",
  applyWithRemainingLabel = t("nl2sql.clarification.applyWithRemaining"),
  onApplyWithRemaining,
  trailingActions,
  idPrefix = "nl2sql-clarification",
}: {
  controller: RequirementSessionController;
  size?: ButtonSize;
  applyWithRemainingLabel?: string;
  onApplyWithRemaining: () => void;
  trailingActions?: ReactNode;
  idPrefix?: string;
}) {
  const {
    activeItem,
    correctingItem,
    state,
    busyAction,
    error,
    selectedCandidateIds,
    freeText,
    answerReady,
    questionHeadingRef,
    showApply,
    unresolvedCount,
    answeredCount,
  } = controller;
  if (!activeItem || !state) return null;
  const applyWithRemaining = showApply && unresolvedCount > 0;
  const heading = correctingItem
    ? correctingItem.question_ja || t("nl2sql.clarification.correctLabel", { label: correctingItem.label_ja })
    : activeItem.question_ja || activeItem.label_ja;
  return (
    <fieldset
      className="grid min-w-0 gap-3 rounded-md border border-border bg-surface-sunken p-4"
      data-testid="nl2sql-clarification-question"
      data-item-key={activeItem.key}
    >
      <legend className="sr-only">{heading}</legend>
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
          <StatusBadge icon={false} variant="neutral" label={t(SECTION_LABEL_KEY[activeItem.section])} />
        </p>
        <h4 ref={questionHeadingRef} tabIndex={-1} className="mt-1 text-sm font-semibold leading-6 text-fg">
          {heading}
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
                  onChange={() => controller.selectCandidate(candidate.id)}
                  className="mt-1 h-4 w-4 accent-accent-emphasis"
                />
                <span className="min-w-0">
                  <span className="block break-words font-medium">{candidate.label_ja}</span>
                  {candidate.description_ja ? (
                    <span className="mt-0.5 block text-xs leading-5 text-fg-muted">{candidate.description_ja}</span>
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
        id={`${idPrefix}-free-text`}
        label={t("nl2sql.clarification.other")}
        value={freeText}
        onChange={(event) => controller.changeFreeText(event.currentTarget.value)}
        disabled={Boolean(busyAction)}
        rows={2}
        placeholder={t("nl2sql.clarification.otherPlaceholder")}
      />
      {!answerReady && error ? (
        <FieldError id={`${idPrefix}-answer-error`} message={t("nl2sql.clarification.answerRequired")} />
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        {correctingItem ? (
          <Button
            type="button"
            variant="ghost"
            size={size}
            icon={X}
            disabled={Boolean(busyAction)}
            onClick={() => controller.setCorrectingItemId(null)}
          >
            {t("nl2sql.clarification.correctCancel")}
          </Button>
        ) : null}
        {applyWithRemaining ? (
          <Button
            type="button"
            variant="secondary"
            size={size}
            icon={CheckCircle2}
            loading={busyAction === "apply"}
            disabled={Boolean(busyAction && busyAction !== "apply")}
            onClick={onApplyWithRemaining}
          >
            {applyWithRemainingLabel}
          </Button>
        ) : null}
        <Button
          icon={Send}
          type="button"
          variant="primary"
          size={size}
          loading={busyAction === "answer"}
          disabled={!answerReady || Boolean(busyAction && busyAction !== "answer")}
          onClick={() => void controller.submitAnswer()}
        >
          {correctingItem ? t("nl2sql.clarification.correctSubmit") : t("nl2sql.clarification.answer")}
        </Button>
        {correctingItem ? null : trailingActions}
      </div>
    </fieldset>
  );
}

/** 「SQL の要件（AI の理解）」の欄（表・列は物理名。推測は「直す」で直せる）。 */
export function RequirementItemsList({
  controller,
  heading = true,
  idPrefix = "nl2sql",
}: {
  controller: RequirementSessionController;
  /** 見出しと説明を出す（Disclosure の中に入れるときは見出しを Disclosure が持つ）。 */
  heading?: boolean;
  idPrefix?: string;
}) {
  const { state, draft, items, activeItem, busyAction } = controller;
  if (!state || !draft) return null;
  return (
    <section
      aria-labelledby={heading ? `${idPrefix}-requirements-title` : undefined}
      aria-label={heading ? undefined : t("nl2sql.clarification.requirements")}
      className="grid min-w-0 gap-3"
      data-testid="nl2sql-clarification-requirements"
    >
      {heading ? (
        <div>
          <h4 id={`${idPrefix}-requirements-title`} className="text-sm font-semibold text-fg">
            {t("nl2sql.clarification.requirements")}
          </h4>
          <p className="mt-0.5 text-xs leading-5 text-fg-muted">{t("nl2sql.clarification.requirementsHint")}</p>
        </div>
      ) : (
        <p className="text-xs leading-5 text-fg-muted">{t("nl2sql.clarification.requirementsHint")}</p>
      )}
      <div className="grid gap-2 lg:grid-cols-2">
        {REQUIREMENT_GROUPS.map((group) => {
          const groupItems = items.filter((item) => group.sections.includes(item.section));
          return (
            <section
              key={group.id}
              aria-labelledby={`${idPrefix}-requirement-group-${group.id}`}
              className="min-w-0 rounded-md border border-border bg-surface-sunken p-3"
              data-testid={`nl2sql-requirement-group-${group.id}`}
            >
              <h5 id={`${idPrefix}-requirement-group-${group.id}`} className="text-xs font-semibold text-fg-muted">
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
                                onClick={() => controller.setCorrectingItemId(item.id)}
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
  );
}

/** 要件のまとめ（書き直した質問・SQL の骨組みと注記）。 */
export function RequirementSummary({
  controller,
  idPrefix = "nl2sql",
  className = "grid gap-3 border-t border-border pt-4",
}: {
  controller: RequirementSessionController;
  idPrefix?: string;
  className?: string;
}) {
  const { state, draft, showFinalSummary } = controller;
  if (!showFinalSummary || !state || !draft) return null;
  return (
    <section
      aria-labelledby={`${idPrefix}-requirement-summary-title`}
      className={className}
      data-testid="nl2sql-clarification-summary"
    >
      <h4 id={`${idPrefix}-requirement-summary-title`} className="text-sm font-semibold text-fg">
        {t("nl2sql.clarification.summaryTitle")}
      </h4>
      <div className="grid gap-1">
        <p className="text-xs font-medium text-fg-muted">{t("nl2sql.clarification.clarifiedQuestion")}</p>
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
  );
}
