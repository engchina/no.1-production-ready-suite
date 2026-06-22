import { describe, expect, it } from "vitest";

import {
  documentChunkSetReingestReason,
  documentChunkSetsNeedReingest,
} from "./KnowledgeBaseDetailClient";
import type { DocumentChunkSet } from "@/lib/api";

function chunkSet(patch: Partial<DocumentChunkSet> = {}): DocumentChunkSet {
  return {
    chunk_set_id: "cs_1",
    extraction_recipe_id: "er_1",
    extraction_status: "materialized",
    extraction_reason: null,
    status: "INDEXED",
    chunk_count: 1,
    vector_count: 1,
    extraction_id: null,
    parser: null,
    preprocess: null,
    knowledge_base_ids: ["kb-1"],
    serving_knowledge_base_ids: ["kb-1"],
    layer_statuses: {
      metadata: { layer_id: "md_1", requested: true, status: "materialized", reason: null },
      graph: { layer_id: "gr_1", requested: true, status: "planned_only", reason: null },
      navigation: { layer_id: "nv_1", requested: true, status: "materialized", reason: null },
    },
    ...patch,
  };
}

describe("KnowledgeBaseDetailClient reingest CTA helpers", () => {
  it("detects extraction recipes that need reingest", () => {
    const sets = [
      chunkSet({
        extraction_status: "needs_reingest",
        extraction_reason: "現在の構築設定で文書を再取込してください。",
      }),
    ];

    expect(documentChunkSetsNeedReingest(sets)).toBe(true);
    expect(documentChunkSetReingestReason(sets)).toBe(
      "現在の構築設定で文書を再取込してください。"
    );
  });

  it("detects derived layers that need reingest", () => {
    const sets = [
      chunkSet({
        layer_statuses: {
          metadata: {
            layer_id: "md_1",
            requested: true,
            status: "needs_reingest",
            reason: "項目抽出には再取込が必要です。",
          },
          graph: { layer_id: "gr_1", requested: true, status: "planned_only", reason: null },
          navigation: { layer_id: "nv_1", requested: true, status: "materialized", reason: null },
        },
      }),
    ];

    expect(documentChunkSetsNeedReingest(sets)).toBe(true);
    expect(documentChunkSetReingestReason(sets)).toBe("項目抽出には再取込が必要です。");
  });

  it("does not show a CTA for planned-only work", () => {
    expect(documentChunkSetsNeedReingest([chunkSet()])).toBe(false);
    expect(documentChunkSetReingestReason([chunkSet()])).toBeNull();
  });
});
