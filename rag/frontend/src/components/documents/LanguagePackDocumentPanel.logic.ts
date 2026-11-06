import type { LanguagePackDocumentStatus, LanguagePackDocumentUse, LanguagePackRecipeUse, LanguagePackRelation } from "@/lib/api";

/** 構築中の job が固定した版。版の行が無ければ version は null（版の数から推定しない）。 */
export interface LanguagePackBuildTarget {
  releaseId: string;
  version: number | null;
}

/** 比較の向き（比較元 → 比較先）。比較元・比較先の両方が記録された版のときだけ作る。 */
export interface LanguagePackComparisonPair {
  beforeId: string;
  afterId: string;
}

/**
 * 文書レシピの索引と構築の状態。
 * - notBuilt: 検索に使う索引が無く、構築中の job も無い。
 * - firstBuild: 検索に使う索引が無く、job が版を固定して構築中。
 * - sameVersionBuild: 検索に使う索引と同じ版で構築中。
 * - differentVersionBuild: 検索に使う索引と別の版で構築中（比較は使用中 → 構築の対象）。
 * - unrecorded: 検索に使う索引はあるが、その版を判定できない（構築中かどうかを問わない）。
 * - built: 検索に使う索引の版が記録済みで、構築中の job は無い（比較は使用中 → 現在版）。
 */
export type LanguagePackPanelState =
  | { kind: "notBuilt" }
  | { kind: "firstBuild"; target: LanguagePackBuildTarget }
  | { kind: "sameVersionBuild"; active: LanguagePackRecipeUse; target: LanguagePackBuildTarget }
  | { kind: "differentVersionBuild"; active: LanguagePackRecipeUse; target: LanguagePackBuildTarget; comparison: LanguagePackComparisonPair }
  | { kind: "unrecorded"; active: LanguagePackRecipeUse; target: LanguagePackBuildTarget | null }
  | { kind: "built"; active: LanguagePackRecipeUse; relation: Exclude<LanguagePackRelation, "unrecorded">; comparison: LanguagePackComparisonPair };

/**
 * 使用版を判定できるか。backend の index_relation と同じく版の id と取込設定の hash の両方を求め、
 * 版の行が無い（版番号を引けない）ときも判定できないものとして扱う。
 */
export function isRecordedUse(use: LanguagePackRecipeUse): use is LanguagePackRecipeUse & { release_id: string; ingest_hash: string; version: number } {
  return Boolean(use.release_id) && Boolean(use.ingest_hash) && use.version !== null;
}

/** 文書レシピ 1 つの言語資源の状態を、索引の有無・使用版の記録・構築中の対象版から決める。 */
export function languagePackPanelState(use: LanguagePackDocumentUse, recipeId: string): LanguagePackPanelState {
  const entries = use.recipes.filter((item) => item.recipe_id === recipeId);
  const active = entries.find((item) => item.chunk_set_id !== null) ?? null;
  const building = entries.find((item) => item.target_release_id);
  const target: LanguagePackBuildTarget | null = building?.target_release_id
    ? { releaseId: building.target_release_id, version: building.target_version ?? null }
    : null;
  if (!active) return target ? { kind: "firstBuild", target } : { kind: "notBuilt" };
  if (!isRecordedUse(active) || active.relation === "unrecorded") return { kind: "unrecorded", active, target };
  if (target) {
    return active.release_id === target.releaseId
      ? { kind: "sameVersionBuild", active, target }
      : { kind: "differentVersionBuild", active, target, comparison: { beforeId: active.release_id, afterId: target.releaseId } };
  }
  return { kind: "built", active, relation: active.relation, comparison: { beforeId: active.release_id, afterId: use.current_release_id } };
}

/**
 * 利用者に見せる状態（バッジ 1 つ）。文書の詳細の要約の行と文書インデックスの列で同じものを使う。
 * - current: 現在版を使用（最新） / equivalent: 版は違うが取込設定は同じ（最新・内容は同じ）
 * - different: 取込設定に差分あり（再処理を推奨） / unrecorded: 使用版を判定できない
 * - building: job が版を固定して待機・実行中 / not_built: 索引も構築中の job も無い
 * 値は文書インデックスの絞り込み（backend の LanguagePackDocumentStatus）と同じ。
 */
export type LanguagePackStatus = LanguagePackDocumentStatus;

export function languagePackStatus(state: LanguagePackPanelState): LanguagePackStatus {
  switch (state.kind) {
    case "notBuilt":
      return "not_built";
    case "firstBuild":
    case "sameVersionBuild":
    case "differentVersionBuild":
      return "building";
    case "unrecorded":
      return state.target ? "building" : "unrecorded";
    case "built":
      return state.relation;
  }
}

/** 要約の行の版。構築中は対象版、検索中の索引の版が記録済みならその版。判定できない・未構築は出さない。 */
export function languagePackDisplayVersion(state: LanguagePackPanelState): number | null {
  if ("target" in state && state.target) return state.target.version;
  return state.kind === "built" ? state.active.version : null;
}

/** 利用者の対応が要る状態か（管理者には「詳細」を開いて出し、権限の無い利用者には依頼を示す）。 */
export function languagePackNeedsAction(status: LanguagePackStatus): boolean {
  return status === "different" || status === "unrecorded";
}

/** 構築中の案内（ページの状態メッセージの枠に 1 本だけ出す）。構築中でなければ null。 */
export interface LanguagePackBuildNotice {
  version: number | null;
  /** 検索に使う索引があり、成功するまでそれを使い続けるか（初回の構築は false）。 */
  keepsActive: boolean;
}

export function languagePackBuildNotice(use: LanguagePackDocumentUse | null | undefined, recipeId: string | null): LanguagePackBuildNotice | null {
  if (!use || !recipeId) return null;
  const state = languagePackPanelState(use, recipeId);
  if (!("target" in state) || !state.target) return null;
  return { version: state.target.version, keepsActive: state.kind !== "firstBuild" };
}

/** 一覧の 1 つのレシピの状態（構築中を先に見る。文書の詳細の判定と同じ）。 */
function recipeStatus(recipe: LanguagePackRecipeUse): LanguagePackStatus {
  if (recipe.target_release_id) return "building";
  if (recipe.chunk_set_id === null) return "not_built";
  return isRecordedUse(recipe) ? recipe.relation : "unrecorded";
}

/** 一覧で 1 つにまとめるときの優先順（対応が要るもの > 構築中 > 問題の無いもの）。 */
const SUMMARY_PRIORITY: readonly LanguagePackStatus[] = ["different", "unrecorded", "building", "equivalent", "current", "not_built"];

/** 一覧の 1 つのラベル。有効レシピの差分・未記録を見落とさず、判定を 1 つにまとめる。 */
export function languagePackSummaryStatus(use: LanguagePackDocumentUse | null | undefined): LanguagePackStatus {
  if (!use) return "unrecorded";
  const statuses = new Set(use.recipes.map(recipeStatus));
  return SUMMARY_PRIORITY.find((status) => statuses.has(status)) ?? "not_built";
}
