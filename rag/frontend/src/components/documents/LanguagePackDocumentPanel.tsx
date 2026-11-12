import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { CircleCheck, CircleHelp, History, Hourglass, RefreshCw, type LucideIcon } from "lucide-react";
import { Banner, Button, Disclosure, FormStatus, SelectField, Skeleton, StatusBadge, TextareaField, TimedLoadingState, useConfirm, type StatusVariant } from "@production-ready/ui";

import { api, type DocumentRecipeView, type IngestionJobPhase, type LanguagePackDocumentUse } from "@/lib/api";
import { t } from "@/lib/i18n";
import { DataLoadFailure } from "@/components/DataLoadStates";
import { ApiErrorState } from "@/components/StateViews";
import { LanguagePackComparison } from "@/components/settings/LanguagePackHistoryCard";
import { formatDateTime } from "@/lib/format";
import { phaseLabelKey } from "./DocumentWorkspace.logic";
import {
  languagePackDisplayVersion,
  languagePackNeedsAction,
  languagePackPanelState,
  languagePackRecipeSummaryStatus,
  languagePackSummaryStatus,
  type LanguagePackBuildNotice,
  type LanguagePackPanelState,
  type LanguagePackStatus,
} from "./LanguagePackDocumentPanel.logic";
import { ALL_START_PHASES, UNREADABLE_START_PHASES, isUnreadableReleaseError, selectablePhase } from "./LanguagePackUnreadable.logic";

/**
 * 状態ごとのバッジの形。対応の要らない状態（最新・未構築・対象外）は控えめな neutral、対応が要る状態は warning、
 * 構築中は info。色だけにしないよう、状態ごとにアイコンも変える（デザインシステム README §4「StatusBadge」）。
 */
const STATUS_BADGE: Record<LanguagePackStatus, { variant: StatusVariant; icon: LucideIcon | true }> = {
  current: { variant: "neutral", icon: CircleCheck },
  equivalent: { variant: "neutral", icon: CircleCheck },
  different: { variant: "warning", icon: true },
  unrecorded: { variant: "warning", icon: CircleHelp },
  building: { variant: "info", icon: Hourglass },
  not_built: { variant: "neutral", icon: true },
  // 旧版は判定・絞り込みの対象外で対応も要らない（neutral）。形は一覧の「旧版」と同じ History（#4601）。
  superseded: { variant: "neutral", icon: History },
};

export function LanguagePackStatusBadge({ status }: { status: LanguagePackStatus }) {
  const badge = STATUS_BADGE[status];
  return <StatusBadge variant={badge.variant} icon={badge.icon} label={t(`settings.language.release.status.${status}`)} />;
}

/** 文書インデックスの列の 1 つのラベル（backend が有効なレシピの状態を 1 つにまとめた状態）。 */
export function LanguagePackUseSummary({ use }: { use?: LanguagePackDocumentUse | null }) {
  return <LanguagePackStatusBadge status={languagePackSummaryStatus(use)} />;
}

/** 構築中の案内の文（ページの状態メッセージの枠の本文）。 */
export function languagePackBuildText(notice: LanguagePackBuildNotice): string {
  const target = notice.version === null ? t("settings.language.release.buildingUnknownVersion") : t("settings.language.release.buildingVersion", { version: notice.version });
  return `${target}${t(notice.keepsActive ? "settings.language.release.buildingKeepActive" : "settings.language.release.buildingFirst")}`;
}

/**
 * 処理レシピの区画に置く、選んだレシピの言語資源の要約の行（#4566）。
 * 「言語資源 v{n} [状態]」の 1 行を全員に出し、言語資源の設定の権限を持つ利用者には「詳細」を足す。
 * 権限の無い利用者には操作を描かず、対応が要るときだけ管理者への依頼を示す（UX 契約 messaging §3.4.1）。
 */
export function LanguagePackRecipeSummary({ documentId, fileName, use, recipe, canManage }: { documentId: string; fileName: string; use?: LanguagePackDocumentUse | null; recipe: DocumentRecipeView; canManage: boolean }) {
  if (!use) return null;
  const state = languagePackPanelState(use, recipe.recipe_id);
  const status = languagePackRecipeSummaryStatus(use, state);
  const version = languagePackDisplayVersion(state);
  const needsAction = languagePackNeedsAction(status);
  return <div className="mt-3 space-y-1 border-t border-border pt-3" data-testid="document-language-pack" data-state={state.kind} data-status={status}>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-sm font-medium text-fg">{version === null ? t("settings.language.release.summary") : t("settings.language.release.summaryVersion", { version })}</span>
      <LanguagePackStatusBadge status={status} />
      {!canManage && needsAction ? <span className="text-xs text-fg-muted" data-testid="document-language-pack-ask-admin">{t("settings.language.release.askAdmin")}</span> : null}
    </div>
    {canManage && state.kind !== "notBuilt" ? <LanguagePackDetails key={`${recipe.recipe_id}:${state.kind}:${needsAction}`} documentId={documentId} fileName={fileName} use={use} recipe={recipe} state={state} defaultOpen={needsAction} /> : null}
  </div>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return <div className="min-w-0">
    <dt className="text-xs text-fg-muted">{label}</dt>
    <dd className="mt-0.5 break-words text-sm text-fg">{children}</dd>
  </div>;
}

/** 公開時の完全な設定。開いたときだけ読む（自動で開いた「詳細」で読み込みを増やさない）。 */
function ReleaseSnapshot({ id, releaseId, label }: { id: string; releaseId: string; label: string }) {
  const [open, setOpen] = useState(false);
  const snapshot = useQuery({ queryKey: ["language-pack-release", releaseId], queryFn: () => api.getLanguagePackRelease(releaseId), enabled: open, retry: false });
  return <Disclosure variant="card" surface="sunken" size="sm" summary={label} open={open} onOpenChange={setOpen} summaryProps={{ "data-testid": `${id}-toggle` }} data-testid={id}>
    {/* 閉じている間は query が無効でも isPending のままなので、読み込み中は開いて取得しているときだけ描く（計時は開いたときから。#4597）。 */}
    {snapshot.isPending ? open ? <TimedLoadingState label={t("settings.language.history.loading")} operationKey={`document-language-release:${releaseId}`}><Skeleton className="h-40 w-full" /></TimedLoadingState> : null
      : snapshot.isError ? isUnreadableReleaseError(snapshot.error) ? <Banner severity="warning">{t("settings.language.release.unreadableSnapshot")}</Banner> : <DataLoadFailure error={snapshot.error} fallback={t("settings.language.release.snapshotLoadError")} onRetry={() => void snapshot.refetch()} layout="field" testId={`${id}-load-failed`} />
      : <TextareaField id={`${id}-value`} label={label} labelHidden rows={8} width="full" readOnly value={JSON.stringify(snapshot.data.effective, null, 2)} />}
  </Disclosure>;
}

function LanguagePackDetails({ documentId, fileName, use, recipe, state, defaultOpen }: { documentId: string; fileName: string; use: LanguagePackDocumentUse; recipe: DocumentRecipeView; state: Exclude<LanguagePackPanelState, { kind: "notBuilt" }>; defaultOpen: boolean }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [open, setOpen] = useState(defaultOpen);
  const [review, setReview] = useState<string | null>(null);
  const [phase, setPhase] = useState<IngestionJobPhase | "">("");
  const active = "active" in state ? state.active : null;
  const target = "target" in state ? state.target : null;
  // 比較は記録済みの別の版どうしだけ（使用中 → 構築の対象、構築中でなければ使用中 → 現在版）。現在版を使う索引は比べない。
  const pair = state.kind === "differentVersionBuild" || (state.kind === "built" && state.relation !== "current") ? state.comparison : null;
  // 構築中でなく使用版を判定できない索引は、差分を出さずに再処理の判断だけを残す。
  const reviewable = pair !== null || (state.kind === "unrecorded" && target === null);
  const reviewKey = `${recipe.recipe_id}:${state.kind}:${active?.chunk_set_id ?? ""}:${pair ? `${pair.beforeId}>${pair.afterId}` : ""}`;
  const reviewed = reviewable && review === reviewKey;
  const comparison = useQuery({ queryKey: ["language-pack-comparison", pair?.beforeId, pair?.afterId], queryFn: () => api.compareLanguagePackReleases(pair!.beforeId, pair!.afterId), enabled: reviewed && pair !== null, retry: false });
  // 比較元の版を現在の契約で読めない（409）ときは比較できないが、ファイル準備か抽出からの再処理で作り直せる。
  const unreadable = comparison.isError && isUnreadableReleaseError(comparison.error);
  const startPhase = selectablePhase(phase, unreadable);
  const processing = target !== null || recipe.steps.some((step) => step.status === "RUNNING" || step.status === "QUEUED");
  const canReprocess = reviewed && ((state.kind === "built" && (state.relation === "different" || unreadable)) || (state.kind === "unrecorded" && target === null));
  const enqueue = useMutation({ mutationFn: (start: IngestionJobPhase) => api.enqueueDocumentRecipeJob(documentId, recipe.recipe_id, start, use.current_release_id, recipe.config_revision), onSuccess: () => { void qc.invalidateQueries({ queryKey: ["documents", documentId] }); } });
  const execute = async () => {
    if (!startPhase || !reviewed) return;
    const ok = await confirm({ title: t("settings.language.release.reprocessTitle"), description: t("settings.language.release.reprocessConfirm", { fileName, recipe: t("documents.recipes.name", { slot: recipe.slot_no }), version: use.current_version, phase: t(phaseLabelKey(startPhase)) }), confirmLabel: t("flow.reprocess.confirm"), tone: "warning" });
    if (ok) enqueue.mutate(startPhase);
  };
  const recorded = state.kind !== "unrecorded" && state.kind !== "firstBuild" && active?.release_id ? active : null;
  return <Disclosure variant="plain" size="sm" summary={t("settings.language.release.detailsToggle")} open={open} onOpenChange={setOpen} summaryProps={{ "data-testid": "document-language-pack-details-toggle" }} data-testid="document-language-pack-details" contentClassName="space-y-4 pt-2">
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
      <Fact label={t("settings.language.release.activeIndex")}>{active ? state.kind === "unrecorded" ? t("settings.language.release.unrecordedActive") : `v${active.version}` : t("settings.language.release.notBuilt")}</Fact>
      {state.kind === "built" ? <Fact label={t("settings.language.release.relationLabel")}>{t(`settings.language.release.relation.${state.relation}`)}</Fact> : null}
      {target ? <Fact label={t("settings.language.release.targetVersionLabel")}>{target.version === null ? t("settings.language.release.versionUnknown") : `v${target.version}`}</Fact> : null}
      <Fact label={t("settings.language.release.currentVersionLabel")}>{`v${use.current_version}`}</Fact>
      {active?.built_at ? <Fact label={t("settings.language.release.builtAt")}><span className="tnum">{formatDateTime(active.built_at)}</span></Fact> : null}
      {active?.job_id ? <Fact label={t("settings.language.release.job")}><span className="break-all font-mono text-xs">{active.job_id}</span></Fact> : null}
    </dl>
    {recorded?.release_id ? <ReleaseSnapshot id="document-language-pack-snapshot" releaseId={recorded.release_id} label={t("settings.language.release.snapshotVersion", { version: recorded.version ?? "" })} /> : null}
    {target && target.releaseId !== recorded?.release_id ? <ReleaseSnapshot id="document-language-pack-target-snapshot" releaseId={target.releaseId} label={target.version === null ? t("settings.language.release.targetSnapshotUnknown") : t("settings.language.release.targetSnapshotVersion", { version: target.version })} /> : null}
    {reviewable ? <div><Button variant="secondary" size="sm" onClick={() => setReview(reviewKey)}>{t(pair ? "settings.language.release.review" : "settings.language.release.reviewReprocess")}</Button></div> : null}
    {reviewed && !pair ? <Banner severity="warning" title={t("settings.language.release.unrecordedActive")}>{t("settings.language.release.unrecordedHelp")}</Banner> : null}
    {reviewed && pair ? comparison.isPending ? <TimedLoadingState label={t("settings.language.history.loading")} operationKey="document-language-diff"><Skeleton className="h-20 w-full" /></TimedLoadingState> : comparison.isError ? unreadable ? <Banner severity="warning" title={t("settings.language.release.unreadableTitle")}>{t("settings.language.release.unreadableHelp")}</Banner> : <ApiErrorState error={comparison.error} fallback={t("settings.language.history.loadError")} onRetry={() => void comparison.refetch()} /> : <LanguagePackComparison comparison={comparison.data} /> : null}
    {canReprocess ? <div className="space-y-3">
      <SelectField id={`recipe-${recipe.recipe_id}-language-phase`} label={t("settings.language.release.startPhase")} value={startPhase} required width="md" options={[{ value: "", label: t("settings.language.release.selectPhase") }, ...(unreadable ? UNREADABLE_START_PHASES : ALL_START_PHASES).map((value) => ({ value, label: t(phaseLabelKey(value)) }))]} onValueChange={(value) => setPhase(value as IngestionJobPhase)} helper={t("settings.language.release.processingHelp")} />
      <Button variant="secondary" icon={RefreshCw} disabled={!startPhase || processing || (comparison.isError && !unreadable) || comparison.isFetching} loading={enqueue.isPending} onClick={() => void execute()}>{t("flow.reprocess.confirm")}</Button>
    </div> : null}
    {enqueue.isError ? <ApiErrorState error={enqueue.error} fallback={t("settings.language.saveError")} onRetry={() => { void qc.invalidateQueries({ queryKey: ["documents", documentId] }); setReview(null); enqueue.reset(); }} /> : null}
    {enqueue.isSuccess ? <FormStatus tone="success" message={t("settings.language.release.queued")} /> : null}
  </Disclosure>;
}
