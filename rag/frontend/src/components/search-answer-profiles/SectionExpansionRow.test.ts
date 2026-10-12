import { describe, expect, it } from "vitest";

import {
  SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT,
  SECTION_EXPANSION_INPUT_TOKEN_PERCENTS,
  SECTION_EXPANSION_MAX_LEVELS_DEFAULT,
  SECTION_EXPANSION_MAX_LEVELS_MAX,
  SECTION_EXPANSION_MAX_LEVELS_MIN,
  SECTION_EXPANSION_TIME_LIMIT_DEFAULT,
  SECTION_EXPANSION_TIME_LIMITS,
  SECTION_EXTRACTION_MAX_CALLS,
  SECTION_EXTRACTION_MAX_CALLS_DEFAULT,
  isSectionExpansionEnabled,
  sectionExpansionLimitValue,
  sectionExpansionPatch,
} from "./SectionExpansionRow";

// #1436: 章節の拡張は検索・回答プロファイルで選ぶ（環境変数は持たない）。既定は on。
describe("当たった根拠の章節から親の章節へ広げる", () => {
  it("on は既定（null）に戻し、off だけを false で保存して、どちらも上限を外す", () => {
    const limits = {
      section_expansion_max_levels: null,
      section_expansion_input_token_percent: null,
      section_expansion_time_limit_seconds: null,
      section_extraction_max_calls: null,
    };
    expect(sectionExpansionPatch(true)).toEqual({ section_expansion_enabled: null, ...limits });
    expect(sectionExpansionPatch(false)).toEqual({ section_expansion_enabled: false, ...limits });
  });

  it("未指定・null は既定の on、false だけが使わない", () => {
    expect(isSectionExpansionEnabled({})).toBe(true);
    expect(isSectionExpansionEnabled({ section_expansion_enabled: null })).toBe(true);
    expect(isSectionExpansionEnabled({ section_expansion_enabled: false })).toBe(false);
  });

  it("既定と同じ上限は保存しない", () => {
    expect(sectionExpansionLimitValue("4", SECTION_EXPANSION_MAX_LEVELS_DEFAULT)).toBeNull();
    expect(sectionExpansionLimitValue("2", SECTION_EXPANSION_MAX_LEVELS_DEFAULT)).toBe(2);
  });

  it("上限の範囲と既定は backend の検証と同じ", () => {
    expect([
      SECTION_EXPANSION_MAX_LEVELS_MIN,
      SECTION_EXPANSION_MAX_LEVELS_DEFAULT,
      SECTION_EXPANSION_MAX_LEVELS_MAX,
    ]).toEqual([1, 4, 8]);
    expect(SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT).toBe(50);
    expect(Math.min(...SECTION_EXPANSION_INPUT_TOKEN_PERCENTS)).toBeGreaterThanOrEqual(10);
    expect(Math.max(...SECTION_EXPANSION_INPUT_TOKEN_PERCENTS)).toBeLessThanOrEqual(90);
    expect(SECTION_EXPANSION_TIME_LIMIT_DEFAULT).toBe(600);
    expect(Math.min(...SECTION_EXPANSION_TIME_LIMITS)).toBeGreaterThanOrEqual(60);
    expect(Math.max(...SECTION_EXPANSION_TIME_LIMITS)).toBeLessThanOrEqual(600);
    // 原文を抜き出す呼び出しの上限（#1437）。backend は 1〜16、既定 8。
    expect(SECTION_EXTRACTION_MAX_CALLS_DEFAULT).toBe(8);
    expect(SECTION_EXTRACTION_MAX_CALLS).toContain(SECTION_EXTRACTION_MAX_CALLS_DEFAULT);
    expect(Math.min(...SECTION_EXTRACTION_MAX_CALLS)).toBeGreaterThanOrEqual(1);
    expect(Math.max(...SECTION_EXTRACTION_MAX_CALLS)).toBeLessThanOrEqual(16);
  });
});
