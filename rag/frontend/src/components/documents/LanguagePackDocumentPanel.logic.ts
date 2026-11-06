import type { LanguagePackDocumentUse, LanguagePackRecipeUse, LanguagePackRelation } from "@/lib/api";

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

/** 一覧の 1 つのラベル。検索に使う索引が無ければ未構築、判定できない索引があれば版未記録。 */
export type LanguagePackSummaryStatus = LanguagePackRelation | "notBuilt";

/** 有効レシピの差分・未記録を見落とさず、一覧では判定を 1 つにまとめる（差分 > 未記録 > 同値 > 現在版）。 */
export function languagePackSummaryStatus(use: LanguagePackDocumentUse | null | undefined): LanguagePackSummaryStatus {
  if (!use) return "unrecorded";
  const actives = use.recipes.filter((recipe) => recipe.chunk_set_id !== null);
  if (actives.length === 0) return "notBuilt";
  const relations = new Set(actives.map((recipe) => (isRecordedUse(recipe) ? recipe.relation : "unrecorded")));
  if (relations.has("different")) return "different";
  if (relations.has("unrecorded")) return "unrecorded";
  return relations.has("equivalent") ? "equivalent" : "current";
}
