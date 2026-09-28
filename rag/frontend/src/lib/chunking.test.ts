import { describe, expect, it } from "vitest";

import {
  CHUNK_SIZE_MAX_CHARS,
  DEFAULT_DOCRAG_CHUNKING_PARAMS,
  invalidDocragChunkingParam,
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

describe("DocRAG 親子階層のパラメータ", () => {
  it("rag_poc と同じ既定値を持つ", () => {
    expect(DEFAULT_DOCRAG_CHUNKING_PARAMS).toEqual({
      docrag_child_target_chars: 1000,
      docrag_table_child_target_chars: 3000,
      docrag_parent_target_chars: 6000,
      docrag_parent_max_pages: 3,
      docrag_parent_max_children: 12,
    });
    expect(invalidDocragChunkingParam(DEFAULT_DOCRAG_CHUNKING_PARAMS)).toBeNull();
  });

  it("範囲外・小数・未入力の項目を返す", () => {
    expect(
      invalidDocragChunkingParam({
        ...DEFAULT_DOCRAG_CHUNKING_PARAMS,
        docrag_table_child_target_chars: 8001,
      })?.field
    ).toBe("docrag_table_child_target_chars");
    expect(
      invalidDocragChunkingParam({ ...DEFAULT_DOCRAG_CHUNKING_PARAMS, docrag_parent_max_pages: 1.5 })
        ?.field
    ).toBe("docrag_parent_max_pages");
    expect(
      invalidDocragChunkingParam({ ...DEFAULT_DOCRAG_CHUNKING_PARAMS, docrag_child_target_chars: null })
        ?.field
    ).toBe("docrag_child_target_chars");
  });
});
