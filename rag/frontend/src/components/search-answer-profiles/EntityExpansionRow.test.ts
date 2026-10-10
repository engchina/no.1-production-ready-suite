import { describe, expect, it } from "vitest";

import {
  ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT,
  ENTITY_EXPANSION_MAX_CHUNKS_MAX,
  ENTITY_EXPANSION_MAX_CHUNKS_MIN,
  entityExpansionPatch,
} from "./EntityExpansionRow";

// #1388: 実体の 1 段の拡張は検索・回答プロファイルで選ぶ（全体の既定は持たない）。
describe("実体でつながる根拠を 1 段広げる", () => {
  it("on は拡張を選び、off は使わない（null）に戻して上限も外す", () => {
    expect(entityExpansionPatch(true)).toEqual({
      entity_expansion_enabled: true,
      entity_expansion_max_chunks: null,
    });
    expect(entityExpansionPatch(false)).toEqual({
      entity_expansion_enabled: null,
      entity_expansion_max_chunks: null,
    });
  });

  it("上限の範囲と既定は backend の検証と同じ", () => {
    expect([
      ENTITY_EXPANSION_MAX_CHUNKS_MIN,
      ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT,
      ENTITY_EXPANSION_MAX_CHUNKS_MAX,
    ]).toEqual([1, 6, 20]);
  });
});
