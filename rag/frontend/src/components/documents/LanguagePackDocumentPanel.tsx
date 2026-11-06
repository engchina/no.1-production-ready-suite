import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button, Card, CardContent, CardHeader, CardTitle, Banner, FormStatus, SelectField, Skeleton, TextareaField, TimedLoadingState, useConfirm, StatusBadge } from "@production-ready/ui";

import { api, type IngestionJobPhase, type LanguagePackDocumentUse, type LanguagePackRelation } from "@/lib/api";
import { t } from "@/lib/i18n";
import { ApiErrorState } from "@/components/StateViews";
import { LanguagePackComparison } from "@/components/settings/LanguagePackHistoryCard";
import { formatDateTime } from "@/lib/format";
import { phaseLabelKey } from "./DocumentWorkspace.logic";
import { languagePackPanelState, languagePackSummaryStatus, type LanguagePackBuildTarget } from "./LanguagePackDocumentPanel.logic";
import { ALL_START_PHASES, UNREADABLE_START_PHASES, isUnreadableReleaseError, selectablePhase } from "./LanguagePackUnreadable.logic";

export function LanguagePackRelationBadge({ relation }: { relation: LanguagePackRelation }) {
  return <StatusBadge variant={relation === "different" ? "warning" : relation === "unrecorded" ? "neutral" : "success"} label={t(`settings.language.release.relation.${relation}`)} icon={false} />;
}

/** 一覧の 1 つのラベル。未構築（索引なし）と版未記録（索引はあるが判定不可）を分ける。 */
export function LanguagePackUseSummary({ use }: { use?: LanguagePackDocumentUse | null }) {
  const status = languagePackSummaryStatus(use);
  if (status === "notBuilt") return <StatusBadge variant="neutral" label={t("settings.language.release.notBuiltLabel")} />;
  return <StatusBadge variant={status === "different" ? "warning" : status === "unrecorded" ? "neutral" : "success"} label={t(`settings.language.release.relation.${status}`)} />;
}

export function LanguagePackDocumentPanel(props: { documentId: string; fileName: string; use?: LanguagePackDocumentUse | null; recipe: import("@/lib/api").DocumentRecipeView | null }) {
  if (!props.use || !props.recipe) return null;
  return <RecipeLanguagePackPanel {...props} use={props.use} recipe={props.recipe} />;
}

function TargetVersion({ target }: { target: LanguagePackBuildTarget }) {
  return <p>{target.version === null ? t("settings.language.release.targetVersionUnknown") : t("settings.language.release.targetVersion", { version: target.version })}</p>;
}

function RecipeLanguagePackPanel({ documentId, fileName, use, recipe }: { documentId: string; fileName: string; use: LanguagePackDocumentUse; recipe: import("@/lib/api").DocumentRecipeView }) {
  const qc = useQueryClient();
  const confirm = useConfirm();
  const [showSnapshot, setShowSnapshot] = useState<string | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const [phase, setPhase] = useState<IngestionJobPhase | "">("");
  const state = languagePackPanelState(use, recipe.recipe_id);
  const active = "active" in state ? state.active : null;
  const target = "target" in state ? state.target : null;
  // 比較は記録済みの版どうしだけ（使用中 → 構築の対象、構築中でなければ使用中 → 現在版）。
  const pair = state.kind === "built" || state.kind === "differentVersionBuild" ? state.comparison : null;
  // 構築中でなく使用版を判定できない索引は、差分を出さずに再処理の判断だけを残す。
  const reviewable = pair !== null || (state.kind === "unrecorded" && target === null);
  const reviewKey = `${recipe.recipe_id}:${state.kind}:${active?.chunk_set_id ?? ""}:${pair ? `${pair.beforeId}>${pair.afterId}` : ""}`;
  const reviewed = reviewable && review === reviewKey;
  const snapshot = useQuery({ queryKey: ["language-pack-release", showSnapshot], queryFn: () => api.getLanguagePackRelease(showSnapshot!), enabled: Boolean(showSnapshot), retry: false });
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
  const targetSnapshotButton = target ? <Button className="mt-3" variant="secondary" size="sm" onClick={() => setShowSnapshot(target.releaseId)}>{t("settings.language.release.targetSnapshot")}</Button> : null;
  return <Card data-testid="document-language-pack" data-state={state.kind}>
    <CardHeader><CardTitle>{t("settings.language.release.usedVersion")}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm">{t("documents.recipes.name", { slot: recipe.slot_no })} · {t("settings.language.release.current", { version: use.current_version })}</p>
      {active ? <div className="flex flex-wrap items-center gap-3">
        <span>{t("settings.language.release.activeIndex")} {state.kind === "unrecorded" ? t("settings.language.release.unrecordedActive") : `v${active.version}`}</span>
        {state.kind === "unrecorded" ? null : <>
          <LanguagePackRelationBadge relation={active.relation} />
          {active.release_id ? <Button size="sm" variant="secondary" onClick={() => setShowSnapshot(active.release_id)}>{t("settings.language.release.snapshot")}</Button> : null}
        </>}
      </div> : <p>{t("settings.language.release.notBuilt")}</p>}
      {active?.built_at ? <p className="text-xs text-fg-muted">{t("settings.language.release.builtAt")} {formatDateTime(active.built_at)}</p> : null}
      {active?.job_id ? <p className="break-all text-xs text-fg-muted">{t("settings.language.release.job")} {active.job_id}</p> : null}
      {state.kind === "firstBuild" ? <Banner severity="info" title={t("settings.language.release.firstBuild")}>
        <TargetVersion target={state.target} />
        <p>{t("settings.language.release.firstBuildHelp")}</p>
        {targetSnapshotButton}
      </Banner> : null}
      {state.kind === "sameVersionBuild" ? <Banner severity="info" title={t("settings.language.release.sameVersionBuilding")}>
        <TargetVersion target={state.target} />
        <p>{t("settings.language.release.sameVersionHelp")}</p>
      </Banner> : null}
      {state.kind === "differentVersionBuild" ? <Banner severity="info" title={t("settings.language.release.building")}>
        <TargetVersion target={state.target} />
        <p>{t("settings.language.release.keepActive")}</p>
        {targetSnapshotButton}
      </Banner> : null}
      {state.kind === "unrecorded" && state.target ? <Banner severity="info" title={t("settings.language.release.unrecordedBuilding")}>
        <TargetVersion target={state.target} />
        <p>{t("settings.language.release.unrecordedBuildingHelp")}</p>
        {targetSnapshotButton}
      </Banner> : null}
      {showSnapshot ? snapshot.isPending ? <TimedLoadingState label={t("settings.language.history.loading")} operationKey="document-language-release"><Skeleton className="h-40 w-full" /></TimedLoadingState> : snapshot.isError ? isUnreadableReleaseError(snapshot.error) ? <Banner severity="warning">{t("settings.language.release.unreadableSnapshot")}</Banner> : <ApiErrorState error={snapshot.error} fallback={t("settings.language.history.loadError")} onRetry={() => void snapshot.refetch()} /> : <TextareaField id={`recipe-${recipe.recipe_id}-language-snapshot`} label={`${t("settings.language.release.snapshot")} v${snapshot.data.version}`} rows={6} width="full" readOnly value={JSON.stringify(snapshot.data.effective, null, 2)} /> : null}
      {reviewable ? <div><Button variant="secondary" onClick={() => setReview(reviewKey)}>{t(pair ? "settings.language.release.review" : "settings.language.release.reviewReprocess")}</Button></div> : null}
      {reviewed && !pair ? <Banner severity="warning" title={t("settings.language.release.unrecordedActive")}>{t("settings.language.release.unrecordedHelp")}</Banner> : null}
      {reviewed && pair ? comparison.isPending ? <TimedLoadingState label={t("settings.language.history.loading")} operationKey="document-language-diff"><Skeleton className="h-20 w-full" /></TimedLoadingState> : comparison.isError ? unreadable ? <Banner severity="warning" title={t("settings.language.release.unreadableTitle")}>{t("settings.language.release.unreadableHelp")}</Banner> : <ApiErrorState error={comparison.error} fallback={t("settings.language.history.loadError")} onRetry={() => void comparison.refetch()} /> : <LanguagePackComparison comparison={comparison.data} /> : null}
      {canReprocess ? <>
        <SelectField id={`recipe-${recipe.recipe_id}-language-phase`} label={t("settings.language.release.startPhase")} value={startPhase} required options={[{ value: "", label: t("settings.language.release.selectPhase") }, ...(unreadable ? UNREADABLE_START_PHASES : ALL_START_PHASES).map((value) => ({ value, label: t(phaseLabelKey(value)) }))]} onValueChange={(value) => setPhase(value as IngestionJobPhase)} />
        <p className="text-xs text-fg-muted">{t("settings.language.release.processingHelp")}</p>
        <Button variant="secondary" icon={RefreshCw} disabled={!startPhase || processing || (comparison.isError && !unreadable) || comparison.isFetching} loading={enqueue.isPending} onClick={() => void execute()}>{t("flow.reprocess.confirm")}</Button>
      </> : null}
      {enqueue.isError ? <ApiErrorState error={enqueue.error} fallback={t("settings.language.saveError")} onRetry={() => { void qc.invalidateQueries({ queryKey: ["documents", documentId] }); setReview(null); enqueue.reset(); }} /> : null}
      {enqueue.isSuccess ? <FormStatus tone="success" message={t("settings.language.release.queued")} /> : null}
    </CardContent>
  </Card>;
}
