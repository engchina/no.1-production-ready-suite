import { describe, expect, it } from "vitest";

import {
  citationAnswerUse,
  citationCountLabel,
  citationMetadataChips,
  citationModelUsed,
  citationSourceUrl,
} from "./chunk-metadata";

describe("citationMetadataChips", () => {
  it("XML ツリー JSON の段落の引用ラベルを先頭の chip にする（issue 2024）", () => {
    expect(
      citationMetadataChips({
        xml_tree_citation_label_arabic: "サンプル規程 第7条第1項",
        section_path: "サンプル規程 > 第七条（対象の範囲）",
      })
    ).toEqual([
      { id: "citation_label", value: "サンプル規程 第7条第1項" },
      { id: "section_path", value: "サンプル規程 > 第七条（対象の範囲）" },
    ]);
  });

  it("ページ範囲・内容種別・章節を chip 化し、章節は階層の 1 つだけにする（issue 5303）", () => {
    // 見出し（section_title）は階層（section_path）の末尾なので別に出さない。分割方針の識別子も出さない。
    expect(
      citationMetadataChips({
        page_start: 2,
        page_end: 4,
        content_kind: "table",
        section_title: "節 B",
        section_path: "章 A > 節 B",
        chunk_profile: "structure_v1",
      })
    ).toEqual([
      { id: "page", value: "2-4" },
      { id: "content_kind", value: "table" },
      { id: "section_path", value: "章 A > 節 B" },
    ]);
  });

  it("節が 1 段の表の行（見出しと階層が同じ値）は章節の chip を 1 つにする（issue 5303）", () => {
    expect(
      citationMetadataChips({
        content_kind: "record",
        section_title: "シート A",
        section_path: "シート A",
        chunk_profile: "structure_v1",
        context_header: "合成の表.xlsx > シート A",
        context_header_enabled: true,
      })
    ).toEqual([
      { id: "content_kind", value: "record" },
      { id: "section_path", value: "シート A" },
    ]);
  });

  it("階層が無ければ見出しを章節の chip にする（issue 5303）", () => {
    expect(citationMetadataChips({ section_title: "節 B", section_path: " " })).toEqual([
      { id: "section_title", value: "節 B" },
    ]);
  });

  it("文脈ヘッダと分割方針は chip にしない（issue 5303）", () => {
    expect(
      citationMetadataChips({ context_header: "文書 A > 節 A", context_header_enabled: false, chunk_profile: "structure_v1" })
    ).toEqual([]);
    expect(citationMetadataChips({})).toEqual([]);
  });

  it("資料に印刷されたページ番号があれば物理頁と並べる（issue 1287）", () => {
    expect(citationMetadataChips({ page_start: 12, page_label_start: "3-4" })).toEqual([
      { id: "page", value: "12", printed: "3-4" },
    ]);
    expect(
      citationMetadataChips({
        page_start: 12,
        page_end: 13,
        page_label_start: "3-4",
        page_label_end: "3-5",
      })
    ).toEqual([{ id: "page", value: "12-13", printed: "3-4〜3-5" }]);
    // 印刷の番号が無い・空・物理頁と同じ表記なら物理頁だけ。
    expect(citationMetadataChips({ page_start: 12 })).toEqual([{ id: "page", value: "12" }]);
    expect(citationMetadataChips({ page_start: 12, page_label_start: " " })).toEqual([
      { id: "page", value: "12" },
    ]);
    expect(citationMetadataChips({ page_start: 12, page_label_start: "12" })).toEqual([
      { id: "page", value: "12" },
    ]);
    // 物理頁が無ければ印刷の番号だけでは出さない（MCP の locator と同じ）。
    expect(citationMetadataChips({ page_label_start: "ii" })).toEqual([]);
  });

  it("欠損値や非文字列 metadata は表示対象にしない", () => {
    expect(
      citationMetadataChips({
        page_start: null,
        page_end: 3,
        content_kind: "",
        chunk_profile: true,
      })
    ).toEqual([]);
  });
});

// AI に渡した件数と内訳の表示（#1208・#2040）。
describe("citationCountLabel", () => {
  const chunk = (metadata: Record<string, boolean | string>) => ({ metadata });

  it("まとまり（親）だけを参照した箇所は「回答で引用」に数えず、「まとまりで参照」に分ける（issue 2710）", () => {
    expect(
      citationCountLabel([
        chunk({ evidence_model_used: true }),
        chunk({ evidence_model_used: false, evidence_parent_used: true }),
        chunk({ evidence_model_used: false }),
      ])
    ).toBe("AI に渡した 3 件 ／ 回答で引用 1 件 ／ まとまりで参照 1 件");
  });

  it("回答で引用したかが分かるときは、使用した件数を添える", () => {
    expect(
      citationCountLabel([
        chunk({ evidence_model_used: true }),
        chunk({ evidence_model_used: false }),
        chunk({ evidence_model_used: false }),
      ])
    ).toBe("AI に渡した 3 件 ／ 回答で引用 1 件");
    // 0 件の項目は出さない。
    expect(citationCountLabel([chunk({ evidence_model_used: false })])).toBe("AI に渡した 1 件");
  });

  it("回答での使われ方は参考の件数だけを添える（該当確認済みは数えない）", () => {
    const chunks = [
      chunk({ evidence_model_used: true, evidence_answer_use: "confirmed" }),
      chunk({ evidence_model_used: true, evidence_answer_use: "confirmed" }),
      chunk({ evidence_model_used: true, evidence_answer_use: "reference" }),
      // 回答で引用していない箇所の使われ方は数えない。
      chunk({ evidence_model_used: false, evidence_answer_use: "confirmed" }),
    ];
    expect(citationCountLabel(chunks)).toBe(
      "AI に渡した 4 件 ／ 回答で引用 3 件 ／ 参考 1 件"
    );
    expect(chunks.map(citationAnswerUse)).toEqual([null, null, "reference", null]);
    expect(
      citationCountLabel([chunk({ evidence_model_used: true, evidence_answer_use: "reference" })])
    ).toBe("AI に渡した 1 件 ／ 回答で引用 1 件 ／ 参考 1 件");
  });

  it("回答を作らない検索の結果（evidence_model_used が無い）は件数だけ", () => {
    expect(citationCountLabel([chunk({}), chunk({ evidence_role: "anchor" })])).toBe("AI に渡した 2 件");
    expect(citationModelUsed(chunk({ evidence_model_used: "true" }))).toBeNull();
  });
});

describe("citationSourceUrl", () => {
  it("https の原典の URL だけを返す（issue 2024）", () => {
    expect(citationSourceUrl({ xml_tree_source_url: "https://example.test/doc/1" })).toBe(
      "https://example.test/doc/1"
    );
    expect(citationSourceUrl({ xml_tree_source_url: "javascript:alert(1)" })).toBeNull();
    expect(citationSourceUrl({ xml_tree_source_url: "http://example.test/doc/1" })).toBeNull();
    expect(citationSourceUrl({})).toBeNull();
  });
});
