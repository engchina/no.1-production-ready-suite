import { describe, expect, it } from "vitest";

import {
  CHUNK_SIZE_MAX_CHARS,
  chunkSizeLabelKey,
  chunkingStrategyPreset,
  isSemanticBoundaryStrategy,
  overlapLabelKey,
} from "./chunking";

describe("chunking strategy presentation", () => {
  it.each(["markdown_heading", "page_level"] as const)(
    "%s は意味境界向けの大きな再分割上限を使う",
    (strategy) => {
      expect(isSemanticBoundaryStrategy(strategy)).toBe(true);
      expect(chunkingStrategyPreset(strategy)).toEqual({
        chunkSize: CHUNK_SIZE_MAX_CHARS,
        overlap: 0,
      });
      expect(overlapLabelKey(strategy)).toBe("settings.chunking.params.semanticOverlap");
    }
  );

  it("通常戦略は 800/120 を使う", () => {
    expect(chunkingStrategyPreset("structure_aware")).toEqual({
      chunkSize: 800,
      overlap: 120,
    });
    expect(chunkSizeLabelKey("structure_aware")).toBe(
      "settings.chunking.params.chunkSize"
    );
  });

  it("見出しとページで再分割上限のラベルを分ける", () => {
    expect(chunkSizeLabelKey("markdown_heading")).toBe(
      "settings.chunking.params.headingSplitLimit"
    );
    expect(chunkSizeLabelKey("page_level")).toBe("settings.chunking.params.pageSplitLimit");
  });
});
