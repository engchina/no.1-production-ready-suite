import { describe, expect, it } from "vitest";

import {
  RECIPE_CONFIG_FIELDS,
  RECIPE_CONFIG_ITEMS,
  globalSettingsHref,
  recipeConfigGroups,
} from "./DocumentProcessingConfigPanel.logic";
import { t } from "@/lib/i18n";

// #523: 要約と上書きの一覧は同じ項目を同じ処理順で出す。
describe("選択中レシピの設定の項目と並び順", () => {
  it("取込の処理順に、3 つの自動進行のゲートを工程の間に置く", () => {
    expect(RECIPE_CONFIG_ITEMS.map((item) => t(item.label))).toEqual([
      "ファイル準備",
      "ファイル準備後に抽出へ進む",
      "文書解析",
      "図・画像を AI で読み取る",
      "メタデータ/項目抽出",
      "ナビゲーション要約(章節木)",
      "章節の抽出規則",
      "抽出後に Chunk 作成へ進む",
      "文書分割",
      "文脈ヘッダを検索対象へ追加",
      "Chunk 後に Embedding / 索引へ進む",
      "実体の索引",
      "関係情報の構築",
    ]);
  });

  it("上書きの件数の対象は項目の正本と同じで、重複しない", () => {
    expect(RECIPE_CONFIG_FIELDS).toEqual(RECIPE_CONFIG_ITEMS.map((item) => item.field));
    expect(new Set(RECIPE_CONFIG_FIELDS).size).toBe(RECIPE_CONFIG_FIELDS.length);
    // 要約にだけ無かった文脈ヘッダも、両方の表示の対象に入る。
    expect(RECIPE_CONFIG_FIELDS).toContain("chunk_context_header_enabled");
  });

  it("工程ごとに分けても、つなげると元の並びに戻る", () => {
    const groups = recipeConfigGroups();
    expect(groups.map((group) => [group.index, t(group.label)])).toEqual([
      [1, "ファイル準備"],
      [2, "抽出"],
      [3, "Chunk 作成"],
      [4, "Embedding / 索引"],
    ]);
    expect(groups.flatMap((group) => group.items)).toEqual([...RECIPE_CONFIG_ITEMS]);
    // 各ゲートは、その前の工程の最後に置く。
    expect(groups.map((group) => group.items.at(-1)?.field)).toEqual([
      "auto_parse_after_preprocess_enabled",
      "auto_chunk_after_extract_enabled",
      "auto_index_after_chunk_enabled",
      "graph_profile",
    ]);
  });

  it("項目の無い工程は見出しを出さない", () => {
    const groups = recipeConfigGroups(
      RECIPE_CONFIG_ITEMS.filter((item) => item.phase !== "INDEX")
    );
    expect(groups.map((group) => group.phase)).toEqual(["PREPROCESS", "EXTRACT", "CHUNK"]);
  });
});

// #528: 「グローバル設定に従う」の各行から、その全体の既定を変える画面へ移動する。
describe("グローバル設定を開くリンク", () => {
  it("実体の索引のほかの 12 項目それぞれに、全体の既定を変える画面がある", () => {
    expect(
      Object.fromEntries(RECIPE_CONFIG_ITEMS.map((item) => [item.field, globalSettingsHref(item)]))
    ).toEqual({
      preprocess_profile: "/settings/preprocess",
      auto_parse_after_preprocess_enabled: "/settings/pipeline#pipeline-gate-auto-parse",
      parser_adapter_backend: "/settings/parser-adapters",
      vision_enabled: "/settings/parser-adapters#post-parse-vision",
      field_extraction_enabled: "/settings/parser-adapters#post-parse-field-extraction",
      navigation_summary_enabled: "/settings/parser-adapters#post-parse-navigation-summary",
      section_rules_mode: "/settings/parser-adapters#post-parse-section-rules",
      auto_chunk_after_extract_enabled: "/settings/pipeline#pipeline-gate-auto-chunk",
      chunking_strategy: "/settings/chunking",
      chunk_context_header_enabled: "/settings/chunking",
      auto_index_after_chunk_enabled: "/settings/pipeline#pipeline-gate-auto-index",
      // 実体の索引は文書ごとに選ぶ（全体の既定は env だけ。画面を持たない。#1388）。
      entity_index_enabled: null,
      graph_profile: "/settings/graph",
    });
  });
});
