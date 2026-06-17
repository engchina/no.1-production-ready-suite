import { describe, expect, it } from "vitest";

import { citationMetadataChips, firstCitationElementId } from "./chunk-metadata";

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
