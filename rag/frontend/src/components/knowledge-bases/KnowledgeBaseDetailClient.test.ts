import { describe, expect, it } from "vitest";

import type { DocumentChunkSet } from "@/lib/api";

import { groupChunkSetsByExtraction } from "./KnowledgeBaseDetailClient";

function cs(overrides: Partial<DocumentChunkSet>): DocumentChunkSet {
  return {
    chunk_set_id: "cs_x",
    status: "INDEXED",
    chunk_count: 1,
    vector_count: 1,
    extraction_id: null,
    parser: null,
    preprocess: null,
    knowledge_base_ids: [],
    serving_knowledge_base_ids: [],
    ...overrides,
  };
}

describe("groupChunkSetsByExtraction", () => {
  it("同じ extraction の chunk_set を 1 グループにまとめ parser/preprocess を保つ", () => {
    const groups = groupChunkSetsByExtraction([
      cs({
        chunk_set_id: "cs_a",
        extraction_id: "ex_1",
        parser: "docling",
        preprocess: "passthrough",
      }),
      cs({
        chunk_set_id: "cs_b",
        extraction_id: "ex_1",
        parser: "docling",
        preprocess: "passthrough",
      }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].parser).toBe("docling");
    expect(groups[0].preprocess).toBe("passthrough");
    expect(groups[0].chunkSets.map((c) => c.chunk_set_id)).toEqual(["cs_a", "cs_b"]);
  });

  it("parser が違う extraction は別グループになる(挿入順を保つ)", () => {
    const groups = groupChunkSetsByExtraction([
      cs({ chunk_set_id: "cs_a", extraction_id: "ex_1", parser: "docling" }),
      cs({ chunk_set_id: "cs_b", extraction_id: "ex_2", parser: "marker" }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.parser)).toEqual(["docling", "marker"]);
  });

  it("extraction_id を持たない旧 chunk_set は各々単独グループにして欠落させない", () => {
    const groups = groupChunkSetsByExtraction([
      cs({ chunk_set_id: "cs_legacy1", extraction_id: null }),
      cs({ chunk_set_id: "cs_legacy2", extraction_id: null }),
    ]);
    expect(groups).toHaveLength(2);
    expect(groups.every((g) => g.extractionId === null)).toBe(true);
  });
});
