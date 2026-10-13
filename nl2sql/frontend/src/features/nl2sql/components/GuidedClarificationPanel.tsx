import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { CheckCircle2, X } from "lucide-react";

import { Button, StatusBadge } from "@production-ready/ui";

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
  useRequirementCheck,
  useRequirementProceed,
  useRequirementSession,
} from "./RequirementClarification";
import {
  REQUIREMENT_APPLY_TO_QUESTION_LABELS,
  RequirementClarificationView,
  type RequirementStartPhase,
} from "./RequirementClarificationView";

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
  /**
   * 要件の検査ができなかったときの「確認せずに SQL を生成」（チャットと同じ操作。#1715）。パネルを閉じ、
   * 入力した質問のまま SQL を生成する。
   */
  onSkip: () => void;
}

/**
 * SQL 生成の画面の AI要件確認（#1439）。業務プロファイルの推薦・確定の後に session を作り、1 問ずつの回答・直し・
 * 要件のまとめ・反映を出す。AI要件確認の基準で、チャットも同じ hook と同じ部品（`RequirementClarificationView`）を
 * 使う（#1715）。この画面だけの違いは入口（「AI要件確認」のボタン・業務プロファイルの推薦と確定）と、確定した
 * 要件の行き先（質問の欄へ反映）。
 */
export function GuidedClarificationPanel({
  question,
  profileId,
  engine,
  allowedObjects,
  onClose,
  onApplyQuestion,
  onSkip,
}: GuidedClarificationPanelProps) {
  const controller = useRequirementSession();
  const { session, busyAction, setError } = controller;
  // 要件の検査（業務プロファイルの推薦・確定・session の作成）の状態と失敗はチャットと同じ hook（#1715）。
  const requirementCheck = useRequirementCheck(true);
  const { run: runCheck } = requirementCheck;
  const { proceed, directing } = useRequirementProceed(controller);
  const starting = requirementCheck.checking || directing;
  const [recommendation, setRecommendation] = useState<OntologyProfileRecommendation | null>(null);
  const [selectedProfileId, setSelectedProfileId] = useState("");
  const [startPhase, setStartPhase] = useState<RequirementStartPhase>("recommend_profile");
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
    setRecommendation(null);
    setError("");
    // 確認が要らない（聞くことが無い）ときは、聞かずに反映して質問の欄へ入れる（チャットと同じ。#1715）。
    await proceed(created, applyToQuestion, () => !signal.aborted);
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
  /** 検査を始める（業務プロファイルの推薦 → 確定 → session の作成）。「もう一度確認」も同じ処理。 */
  const beginCheck = () => {
    startControllerRef.current?.abort();
    const abort = new AbortController();
    startControllerRef.current = abort;
    setError("");
    setStartPhase("recommend_profile");
    void runCheck(
      async () => {
        const result = await recommendOntologyProfiles(question, { signal: abort.signal });
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
      },
      { isCurrent: () => !abort.signal.aborted }
    ).finally(() => {
      if (startControllerRef.current === abort) startControllerRef.current = null;
    });
  };
  const beginCheckRef = useRef(beginCheck);
  useLayoutEffect(() => {
    beginCheckRef.current = beginCheck;
  });
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    beginCheckRef.current();
  }, []);

  const confirmRecommendedProfile = async () => {
    if (!recommendation || !selectedProfileId || busyAction || starting) return;
    const abort = new AbortController();
    startControllerRef.current = abort;
    setError("");
    await runCheck(() => startSession(recommendation, selectedProfileId, abort.signal), {
      isCurrent: () => !abort.signal.aborted,
    });
    if (startControllerRef.current === abort) startControllerRef.current = null;
  };

  /** 確定した要件を質問の欄へ反映する（この画面の行き先。チャットは SQL の生成）。 */
  function applyToQuestion(applied: QuerySession) {
    releasedRef.current = true;
    onApplyQuestion(
      applied.requirement?.clarified_question_ja?.trim() ?? "",
      applied.profile_id,
      applied.id
    );
  }

  const applyRequirement = async () => {
    const applied = await controller.applyRequirement();
    if (!applied) return;
    applyToQuestion(applied);
  };

  const closePanel = async () => {
    if (starting) {
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

  // 入口の前段: 「ALL」の業務プロファイルのときは、推薦から使う業務プロファイルを選ぶ。
  const profileChooser =
    recommendation && !session ? (
      <fieldset className="grid gap-3 rounded-md border border-border bg-surface-sunken p-4">
        <legend className="px-1 text-sm font-semibold text-fg">
          {t("nl2sql.clarification.profileQuestion")}
        </legend>
        <p className="text-xs leading-5 text-fg-muted">{t("nl2sql.clarification.profileReason")}</p>
        <div className="grid gap-2">
          {recommendation.candidates.slice(0, 3).map((candidate, index) => (
            <label
              key={`${candidate.profile_id}:${candidate.ontology_revision_id}`}
              data-surface-tint="accent"
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
                  {index === 0 ? (
                    <StatusBadge icon={false} variant="info" label={t("nl2sql.clarification.recommended")} />
                  ) : null}
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
            disabled={!selectedProfileId || Boolean(busyAction) || starting}
            icon={CheckCircle2}
            onClick={() => void confirmRecommendedProfile()}
          >
            {t("nl2sql.clarification.confirmProfile")}
          </Button>
          {closeButton}
        </div>
      </fieldset>
    ) : null;

  return (
    <RequirementClarificationView
      controller={controller}
      check={{ ...requirementCheck, checking: starting }}
      startPhase={startPhase}
      onRetry={() => (recommendation ? void confirmRecommendedProfile() : beginCheck())}
      onSkip={() => {
        startControllerRef.current?.abort();
        onSkip();
      }}
      onApply={() => void applyRequirement()}
      onClose={() => void closePanel()}
      labels={REQUIREMENT_APPLY_TO_QUESTION_LABELS}
      startSlot={profileChooser}
      size="lg"
      idPrefix="nl2sql"
    />
  );
}
