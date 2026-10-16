import { describe, expect, it } from "vitest";

import { chunkingPayload } from "./ChunkingSettingsClient";
import { referenceTextsFromVocabulary, referenceVocabularyErrors } from "./ReferenceVocabularyCard";

const VOCABULARY = {
  document_suffixes: ["マニュアル", "規程"],
  numbered_document_suffixes: ["法"],
  excluded_name_endings: ["方法"],
  generic_names: ["システム"],
  demonstrative_words: ["本", "同"],
  self_words: ["本"],
  part_prefixes: ["附則"],
  separate_part_patterns: ["第N号"],
  branch_connectors: ["の"],
  previous_words: ["前"],
  next_words: ["次"],
  same_words: ["同"],
};

const SAVED: Parameters<typeof chunkingPayload>[0] = {
  strategy: "structure_aware",
  chunk_size: 800,
  overlap: 120,
  min_chars: 120,
  delimiter: "\\n\\n",
  context_header_enabled: true,
  chunk_child_target_chars: 1000,
  chunk_table_child_target_chars: 3000,
  chunk_parent_target_chars: 6000,
  chunk_parent_max_pages: 3,
  chunk_parent_max_children: 12,
  reference_text: referenceTextsFromVocabulary(VOCABULARY),
};
const TEXTS = referenceTextsFromVocabulary(VOCABULARY);

describe("chunkingPayload（Issue 966）", () => {
  it("選んだ方式で出していない欄の空・範囲外は保存済みの値で送る", () => {
    const payload = chunkingPayload(
      {
        ...SAVED,
        strategy: "small_to_big",
        min_chars: Number.NaN,
        delimiter: "  ",
      },
      SAVED
    );
    expect(payload.min_chars).toBe(120);
    expect(payload.delimiter).toBe("\\n\\n");
  });

  it("隠れた欄でも正しい値は利用者の入力のまま送る", () => {
    const payload = chunkingPayload({ ...SAVED, strategy: "fixed_delimiter", min_chars: 40 }, SAVED);
    expect(payload.min_chars).toBe(40);
  });

  it("出している欄は置き換えない（画面の検証が欄の直下に出す）", () => {
    const payload = chunkingPayload(
      { ...SAVED, strategy: "small_to_big", chunk_child_target_chars: Number.NaN, min_chars: 5000 },
      SAVED
    );
    expect(payload.chunk_child_target_chars).toBeNaN();
    expect(payload.min_chars).toBe(120);
  });
});

describe("交差参照の解析の語（Issue 2157）", () => {
  it("1 行 1 語の入力を、空行・前後の空白・重複を除いた一覧で送る", () => {
    const payload = chunkingPayload(
      { ...SAVED, reference_text: { ...TEXTS, document_suffixes: " 要項\n\n手引き\n要項\n", part_prefixes: "" } },
      SAVED
    );
    expect(payload.reference_vocabulary?.document_suffixes).toEqual(["要項", "手引き"]);
    expect(payload.reference_vocabulary?.part_prefixes).toEqual([]);
    expect(payload.reference_vocabulary?.branch_connectors).toEqual(["の"]);
    expect(payload).not.toHaveProperty("reference_text");
  });

  it("番号の位置（N）が無い部の印は欄のエラーにする", () => {
    expect(referenceVocabularyErrors({ ...TEXTS, separate_part_patterns: "第N号\n改正" })).toHaveProperty(
      "separate_part_patterns"
    );
    expect(referenceVocabularyErrors(TEXTS)).toEqual({});
  });

  it("backend が語を返さなければ送らない（空の一覧で上書きしない）", () => {
    expect(chunkingPayload({ ...SAVED, reference_text: null }, SAVED)).not.toHaveProperty("reference_vocabulary");
  });
});
