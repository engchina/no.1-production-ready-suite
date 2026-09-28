import type { ChunkingStrategyName } from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

export const CHUNK_SIZE_MIN_CHARS = 200;
export const CHUNK_SIZE_MAX_CHARS = 32_000;
export const CHUNK_OVERLAP_MAX_CHARS = 8_000;

export type ChunkingPreset = {
  chunkSize: number;
  overlap: number;
};

export function isSemanticBoundaryStrategy(strategy: ChunkingStrategyName): boolean {
  return strategy === "markdown_heading" || strategy === "page_level";
}

export function chunkingStrategyPreset(strategy: ChunkingStrategyName): ChunkingPreset {
  return isSemanticBoundaryStrategy(strategy)
    ? { chunkSize: CHUNK_SIZE_MAX_CHARS, overlap: 0 }
    : { chunkSize: 800, overlap: 120 };
}

export function chunkSizeLabelKey(strategy: ChunkingStrategyName): I18nKey {
  if (strategy === "markdown_heading") return "settings.chunking.params.headingSplitLimit";
  if (strategy === "page_level") return "settings.chunking.params.pageSplitLimit";
  return "settings.chunking.params.chunkSize";
}

export function overlapLabelKey(strategy: ChunkingStrategyName): I18nKey {
  return isSemanticBoundaryStrategy(strategy)
    ? "settings.chunking.params.semanticOverlap"
    : "settings.chunking.params.overlap";
}

export const DOCRAG_CHUNKING_STRATEGY: ChunkingStrategyName = "docrag_small_to_big";

export type DocragChunkingParamField =
  | "docrag_child_target_chars"
  | "docrag_table_child_target_chars"
  | "docrag_parent_target_chars"
  | "docrag_parent_max_pages"
  | "docrag_parent_max_children";

export type DocragChunkingParams = Record<DocragChunkingParamField, number>;

export type DocragChunkingParamSpec = {
  field: DocragChunkingParamField;
  labelKey: I18nKey;
  hintKey: I18nKey;
  min: number;
  max: number;
  step: number;
  defaultValue: number;
};

/**
 * DocRAG 親子階層の分割パラメータ。既定値・範囲・刻みは rag_poc の
 * docrag.chunking.constants(DEFAULT_* / *_RANGE)と同じ(backend の Settings と一致させる)。
 */
export const DOCRAG_CHUNKING_PARAMS: readonly DocragChunkingParamSpec[] = [
  {
    field: "docrag_child_target_chars",
    labelKey: "settings.chunking.params.docragChildTargetChars",
    hintKey: "settings.chunking.params.docragChildTargetCharsHint",
    min: 300,
    max: 1600,
    step: 50,
    defaultValue: 1000,
  },
  {
    field: "docrag_table_child_target_chars",
    labelKey: "settings.chunking.params.docragTableChildTargetChars",
    hintKey: "settings.chunking.params.docragTableChildTargetCharsHint",
    min: 300,
    max: 8000,
    step: 100,
    defaultValue: 3000,
  },
  {
    field: "docrag_parent_target_chars",
    labelKey: "settings.chunking.params.docragParentTargetChars",
    hintKey: "settings.chunking.params.docragParentTargetCharsHint",
    min: 1200,
    max: 10000,
    step: 100,
    defaultValue: 6000,
  },
  {
    field: "docrag_parent_max_pages",
    labelKey: "settings.chunking.params.docragParentMaxPages",
    hintKey: "settings.chunking.params.docragParentMaxPagesHint",
    min: 1,
    max: 5,
    step: 1,
    defaultValue: 3,
  },
  {
    field: "docrag_parent_max_children",
    labelKey: "settings.chunking.params.docragParentMaxChildren",
    hintKey: "settings.chunking.params.docragParentMaxChildrenHint",
    min: 3,
    max: 20,
    step: 1,
    defaultValue: 12,
  },
];

export const DEFAULT_DOCRAG_CHUNKING_PARAMS: DocragChunkingParams = Object.fromEntries(
  DOCRAG_CHUNKING_PARAMS.map((spec) => [spec.field, spec.defaultValue])
) as DocragChunkingParams;

/** 範囲外・未入力の DocRAG パラメータがあれば、最初のその項目の定義を返す。 */
export function invalidDocragChunkingParam(
  values: Partial<Record<DocragChunkingParamField, number | null | undefined>>
): DocragChunkingParamSpec | null {
  return (
    DOCRAG_CHUNKING_PARAMS.find((spec) => {
      const value = values[spec.field];
      return (
        typeof value !== "number" ||
        !Number.isInteger(value) ||
        value < spec.min ||
        value > spec.max
      );
    }) ?? null
  );
}
