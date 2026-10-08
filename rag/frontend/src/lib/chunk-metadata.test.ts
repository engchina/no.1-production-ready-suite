import { describe, expect, it } from "vitest";

import {
  citationCountLabel,
  citationMetadataChips,
  citationModelUsed,
  firstCitationElementId,
} from "./chunk-metadata";

describe("citationMetadataChips", () => {
  it("ページ範囲と構造 metadata を chip 化する", () => {
    expect(
      citationMetadataChips({
        page_start: 2,
        page_end: 4,
        content_kind: "table",
        section_title: "料金表",
        section_path: "契約 > 料金表",
        chunk_profile: "structure_v1",
      })
    ).toEqual([
      { id: "page", value: "2-4" },
      { id: "content_kind", value: "table" },
      { id: "section_title", value: "料金表" },
      { id: "section_path", value: "契約 > 料金表" },
      { id: "chunk_profile", value: "structure_v1" },
    ]);
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

  it("citation preview 用の先頭 element id を複数 metadata 形態から取り出す", () => {
    expect(firstCitationElementId(" tbl-1, el-2 ")).toBe("tbl-1");
    expect(firstCitationElementId(["", " el-3 ", "el-4"])).toBe("el-3");
    expect(firstCitationElementId(42)).toBe("42");
    expect(firstCitationElementId([null, false, ""])).toBeNull();
    expect(firstCitationElementId(null)).toBeNull();
  });
});

// 根拠の件数の表示（#1208）。
describe("citationCountLabel", () => {
  const chunk = (metadata: Record<string, boolean | string>) => ({ metadata });

  it("回答に使ったかが分かるときは、使った件数を添える", () => {
    expect(
      citationCountLabel([
        chunk({ evidence_model_used: true }),
        chunk({ evidence_model_used: false }),
        chunk({ evidence_model_used: false }),
      ])
    ).toBe("根拠 3 件（回答に使用 1 件）");
    expect(citationCountLabel([chunk({ evidence_model_used: false })])).toBe(
      "根拠 1 件（回答に使用 0 件）"
    );
  });

  it("回答を作らない検索の結果（evidence_model_used が無い）は件数だけ", () => {
    expect(citationCountLabel([chunk({}), chunk({ evidence_role: "anchor" })])).toBe("根拠 2 件");
    expect(citationModelUsed(chunk({ evidence_model_used: "true" }))).toBeNull();
  });
});
