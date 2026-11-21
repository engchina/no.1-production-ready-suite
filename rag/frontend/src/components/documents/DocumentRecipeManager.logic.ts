import type {
  DocumentChunkSet,
  DocumentLayerStatusName,
  DocumentRecipeView,
  DocumentProcessingConfig,
  ParserAdapterBackend,
} from "@/lib/api";

import { SMALL_TO_BIG_PARAMS } from "@/lib/chunking";
import { t, type I18nKey } from "@/lib/i18n";
import { parserBackendLabel } from "@/lib/source-profile-labels";
import { RECIPE_CONFIG_ITEMS } from "./DocumentProcessingConfigPanel.logic";

const recipeFieldLabels = new Map<string, I18nKey>([
  ...RECIPE_CONFIG_ITEMS.map(({ field, label }) => [field, label] as const),
  ...SMALL_TO_BIG_PARAMS.map(({ field, labelKey }) => [field, labelKey] as const),
  ["excel_options", "documents.excelOptions.title"],
  ["entity_name_columns", "documents.entityIndex.columns.names"],
  ["entity_attribute_columns", "documents.entityIndex.columns.attributes"],
  ["chunk_size", "settings.chunking.params.chunkSize"],
  ["chunk_overlap", "settings.chunking.params.overlap"],
  ["chunk_min_chars", "settings.chunking.params.minChars"],
  ["chunk_delimiter", "settings.chunking.params.delimiter"],
  ["chunk_separators", "documents.recipes.reprocess.separators"],
]);

const parserFlags = {
  parser_docling_enabled: "docling",
  parser_unstructured_enabled: "unstructured",
  parser_dots_ocr_enabled: "dots_ocr",
  parser_structured_records_jsonl_enabled: "structured_records_jsonl",
  parser_xml_tree_json_enabled: "xml_tree_json",
} as const satisfies Record<Extract<keyof DocumentProcessingConfig, `parser_${string}_enabled`>, ParserAdapterBackend>;

export function recipeReprocessingReason(recipe: DocumentRecipeView): string {
  const fields = new Set(recipe.reprocessing_changed_fields);
  const labels: string[] = [];
  for (const field of fields) {
    const label = recipeFieldLabels.get(field);
    if (label) {
      labels.push(t(label));
      continue;
    }
    if (Object.hasOwn(parserFlags, field)) {
      const parser = parserFlags[field as keyof typeof parserFlags];
      labels.push(t("documents.recipes.reprocess.parserEnabled", { engine: parserBackendLabel(parser) }));
    }
  }
  if (labels.length && labels.length < fields.size) {
    labels.push(t("documents.recipes.reprocess.otherFields", { count: fields.size - labels.length }));
  }
  const current = recipe.config_revision;
  const previous = recipe.materialized_revision;
  const changes = labels.length
    ? t(previous === null ? "documents.recipes.reprocess.fields" : "documents.recipes.reprocess.indexChanges", {
        fields: labels.join(t("documents.recipes.layerRebuildJoin")),
      })
    : t("documents.recipes.reprocess.unknownFields");
  const revisions = previous === null
    ? t("documents.recipes.reprocess.noRevision", { current })
    : previous === current
      ? ""
      : t("documents.recipes.reprocess.revisions", { previous, current });
  const search = t(recipe.searchable ? "documents.recipes.reprocess.searchable" : "documents.recipes.reprocess.unsearchable");
  return changes + revisions + search;
}

export function resolveSelectedRecipe(
  recipes: DocumentRecipeView[],
  requestedRecipeId: string | null
) {
  return recipes.find((recipe) => recipe.recipe_id === requestedRecipeId) ?? recipes[0] ?? null;
}

export function recipeIsActive(recipe: DocumentRecipeView) {
  return recipe.steps.some((step) => step.status === "QUEUED" || step.status === "RUNNING");
}

const EDITABLE_STATUSES: DocumentRecipeView["status"][] = ["UPLOADED", "INDEXED", "ERROR"];

/** 処理中・確認待ちのレシピは処理設定編集を禁止する(実行中ジョブに限らない)。 */
export function recipeConfigLocked(recipe: DocumentRecipeView) {
  return recipeIsActive(recipe) || !EDITABLE_STATUSES.includes(recipe.status);
}

export function canAddRecipe(recipeCount: number) {
  return recipeCount < 3;
}

export function canDeleteRecipe(recipeCount: number, active: boolean) {
  return recipeCount > 1 && !active;
}

const LAYER_ORDER = ["metadata", "navigation"] as const;

export type RecipeLayerName = (typeof LAYER_ORDER)[number];

export interface RecipeLayerStatusView {
  layer: RecipeLayerName;
  status: DocumentLayerStatusName;
  reason: string | null;
  /** 作成後に入力（項目の定義など）が変わり、作り直しが必要か（#550）。 */
  rebuildRequired: boolean;
  rebuildInputs: string[];
}

/** 選択レシピの active chunk_set から派生 layer(項目抽出/ナビ)の状態を引く。 */
export function recipeLayerStatuses(
  recipe: DocumentRecipeView,
  chunkSets: DocumentChunkSet[] | undefined
): RecipeLayerStatusView[] {
  if (!recipe.active_chunk_set_id || !chunkSets?.length) return [];
  const chunkSet = chunkSets.find((set) => set.chunk_set_id === recipe.active_chunk_set_id);
  if (!chunkSet?.layer_statuses) return [];
  return LAYER_ORDER.flatMap((layer) => {
    const status = chunkSet.layer_statuses[layer];
    if (!status?.requested || status.status === "not_requested") return [];
    return [
      {
        layer,
        status: status.status,
        reason: status.reason,
        rebuildRequired: Boolean(status.rebuild_required),
        rebuildInputs: status.rebuild_inputs ?? [],
      },
    ];
  });
}

/** 作り直しが必要なレイヤーと、変わった入力（重複なし・出現順）をまとめる。 */
export function recipeLayerRebuildSummary(statuses: RecipeLayerStatusView[]) {
  const layers = statuses.filter((entry) => entry.rebuildRequired);
  const inputs = [...new Set(layers.flatMap((entry) => entry.rebuildInputs))];
  return { layers: layers.map((entry) => entry.layer), inputs };
}

