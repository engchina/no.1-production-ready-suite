import { describe, expect, it } from "vitest";

import {
  ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT,
  ENTITY_EXPANSION_MAX_CHUNKS_MAX,
  ENTITY_EXPANSION_MAX_CHUNKS_MIN,
  entityExpansionPatch,
  isEntityExpansionEnabled,
} from "./EntityExpansionRow";

// #1388: 実体の 1 段の拡張は検索・回答プロファイルで選ぶ（環境変数は持たない）。既定は on（#1402）。
describe("実体でつながる根拠を 1 段広げる", () => {
  it("on は既定（null）に戻し、off だけを false で保存して、どちらも上限を外す", () => {
    expect(entityExpansionPatch(true)).toEqual({
      entity_expansion_enabled: null,
      entity_expansion_max_chunks: null,
    });
    expect(entityExpansionPatch(false)).toEqual({
      entity_expansion_enabled: false,
      entity_expansion_max_chunks: null,
    });
  });

  it("未指定・null は既定の on、明示した値はそのまま読む", () => {
    expect(isEntityExpansionEnabled({})).toBe(true);
    expect(isEntityExpansionEnabled({ entity_expansion_enabled: null })).toBe(true);
    expect(isEntityExpansionEnabled({ entity_expansion_enabled: true })).toBe(true);
    expect(isEntityExpansionEnabled({ entity_expansion_enabled: false })).toBe(false);
  });

  it("上限の範囲と既定は backend の検証と同じ", () => {
    expect([
      ENTITY_EXPANSION_MAX_CHUNKS_MIN,
      ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT,
      ENTITY_EXPANSION_MAX_CHUNKS_MAX,
    ]).toEqual([1, 6, 20]);
  });
});
