import { describe, expect, it } from "vitest";

import { parseStructuredExtraction, summarizeDocumentElements } from "./extraction";

describe("parseStructuredExtraction", () => {
  it("elements と raw_text を安全に読み取る", () => {
    const parsed = parseStructuredExtraction({
      raw_text: "本文",
      document_type: "規程",
      confidence: 0.91,
      warnings: ["低解像度"],
      elements: [
        {
          kind: "table",
          text: "| 項目 | 金額 |",
          order: 2,
          element_id: "tbl-1",
          content_kind: "table",
          source_parser: "local_office_structure",
          page_number: 2,
          bbox: [0, 0, 100, 50],
          section_path: ["経費申請"],
          confidence: 0.8,
          metadata: { raw_start: 10, nested: { ignored: true } },
        },
        {
          kind: "title",
          text: "経費申請",
          order: 1,
          page_number: 1,
        },
      ],
      pages: [{ page_number: 2, element_ids: ["tbl-1"] }],
      tables: [
        {
          table_id: "table-1",
          element_id: "tbl-1",
          cells: [
            {
              row: 0,
              col: 0,
              text: "項目",
              page_number: 2,
              metadata: { formula_cell_ref: "B2", nested: { ignored: true } },
            },
          ],
        },
      ],
      assets: [{ asset_id: "fig-1", kind: "figure", page_number: 2 }],
      parser_artifacts: { parser_backend: "local_partition" },
    });

    expect(parsed.rawText).toBe("本文");
    expect(parsed.documentType).toBe("規程");
    expect(parsed.confidence).toBe(0.91);
    expect(parsed.warnings).toEqual(["低解像度"]);
    expect(parsed.elements.map((element) => element.kind)).toEqual(["title", "table"]);
    expect(parsed.elements[1].element_id).toBe("tbl-1");
    expect(parsed.elements[1].content_kind).toBe("table");
    expect(parsed.elements[1].source_parser).toBe("local_office_structure");
    expect(parsed.elements[1].bbox).toEqual([0, 0, 100, 50]);
    expect(parsed.elements[1].metadata).toEqual({ raw_start: 10 });
    expect(parsed.pages[0].element_ids).toEqual(["tbl-1"]);
    expect(parsed.tables[0].cells[0].text).toBe("項目");
    expect(parsed.tables[0].cells[0].page_number).toBe(2);
    expect(parsed.tables[0].cells[0].metadata).toEqual({ formula_cell_ref: "B2" });
    expect(parsed.assets[0].asset_id).toBe("fig-1");
    expect(parsed.parserArtifacts.parser_backend).toBe("local_partition");
  });

  it("不完全な旧 extraction は raw text fallback として扱う", () => {
    const parsed = parseStructuredExtraction({
      raw_text: "旧データの本文だけ",
      elements: [{ kind: "text", text: "" }],
      confidence: "bad",
      warnings: "bad",
    });

    expect(parsed.rawText).toBe("旧データの本文だけ");
    expect(parsed.confidence).toBeNull();
    expect(parsed.warnings).toEqual([]);
    expect(parsed.elements).toEqual([]);
  });
});

describe("summarizeDocumentElements", () => {
  it("種別とページを集計する", () => {
    const stats = summarizeDocumentElements([
      { kind: "title", text: "A", order: 0, page_number: 1 },
      { kind: "list", text: "- A", order: 1, page_number: 1 },
      { kind: "table", text: "| A |", order: 2, page_number: 2 },
      { kind: "text", text: "本文", order: 3, page_number: 2 },
    ]);

    expect(stats).toEqual({
      elementCount: 4,
      titleCount: 1,
      textCount: 1,
      tableCount: 1,
      listCount: 1,
      pageCount: 2,
    });
  });
});
