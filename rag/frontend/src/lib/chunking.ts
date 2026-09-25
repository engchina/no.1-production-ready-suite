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
