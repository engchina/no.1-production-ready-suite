import { describe, expect, it } from "vitest";

import type { VectorIndexSettingsData } from "@/lib/api";

import { currentIndexLabel, reprovisionStatus, showReindexSql } from "./VectorIndexSettingsClient";

function settings(overrides: Partial<VectorIndexSettingsData>): VectorIndexSettingsData {
  return {
    profile: "accurate",
    target_accuracy: 98,
    neighbors: 48,
    efconstruction: 800,
    distance: "COSINE",
    requires_reprovision: false,
    index_status: "match",
    actual_neighbors: 48,
    actual_efconstruction: 800,
    profiles: [],
    reindex_sql: "DROP INDEX rag_chunks_embedding_hnsw_idx;",
    config_source: "runtime",
    ...overrides,
  };
}

describe("reprovisionStatus", () => {
  it("backend の判定が一致なら警告を出さない", () => {
    expect(reprovisionStatus("match")).toBeNull();
    expect(reprovisionStatus(undefined)).toBeNull();
  });

  it("違うときは再作成の警告、確認できないときは確認できない旨を出す", () => {
    expect(reprovisionStatus("reprovision")).toEqual({
      tone: "warning",
      key: "settings.vectorIndex.reprovision",
    });
    expect(reprovisionStatus("unknown")).toEqual({
      tone: "info",
      key: "settings.vectorIndex.reprovisionUnknown",
    });
  });
});

describe("showReindexSql", () => {
  it("一致なら再作成 SQL を出さず、違う・確認できないときは出す", () => {
    expect(showReindexSql(settings({ index_status: "match" }))).toBe(false);
    expect(showReindexSql(settings({ index_status: "reprovision" }))).toBe(true);
    expect(showReindexSql(settings({ index_status: "unknown" }))).toBe(true);
    expect(showReindexSql(settings({ index_status: "unknown", reindex_sql: "" }))).toBe(false);
  });
});

describe("currentIndexLabel", () => {
  it("実際の索引の値を出し、読めないときは確認できない旨を出す", () => {
    expect(currentIndexLabel(settings({}))).toBe("NEIGHBORS 48 / EFCONSTRUCTION 800");
    expect(
      currentIndexLabel(settings({ actual_neighbors: null, actual_efconstruction: null }))
    ).toBe("確認できません");
  });
});
