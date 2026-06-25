import { describe, expect, it } from "vitest";

import type { RetrievedChunk } from "@/lib/api";
import { citationPreviewUrl, scoreMeterPercent, variantIdFromChunkId } from "./CitationCard";

describe("variantIdFromChunkId", () => {
  it("document:chunk_set:index 形式から chunk_set(variant)id を取り出す", () => {
    expect(variantIdFromChunkId("doc-1:cs_abc123:0")).toBe("cs_abc123");
  });

  it("chunk_set タグの無い document:index は variant 無し(null)", () => {
    expect(variantIdFromChunkId("doc-1:7")).toBeNull();
  });
});

describe("scoreMeterPercent", () => {
  it("最大値に対する相対幅を返す", () => {
    expect(scoreMeterPercent(0.5, 1)).toBe(50);
  });

  it("null / 0 / 負値 / 最大値超過を安全に丸める", () => {
    expect(scoreMeterPercent(null, 1)).toBe(0);
    expect(scoreMeterPercent(0, 1)).toBe(0);
    expect(scoreMeterPercent(-0.1, 1)).toBe(0);
    expect(scoreMeterPercent(0.5, 0)).toBe(0);
    expect(scoreMeterPercent(1.5, 1)).toBe(100);
  });
});

describe("citationPreviewUrl", () => {
  it("引用位置 deep link に page/bbox/table cell lineage を含める", () => {
    const url = citationPreviewUrl({
      document_id: "doc-1",
      chunk_id: "doc-1:7",
      text: "根拠",
      score: 0.91,
      rerank_score: null,
      file_name: "policy.pdf",
      category_name: null,
      metadata: {
        page_start: 3,
        bbox: [10, 20, 70, 45],
        bbox_coordinate_mode: "xyxy",
        bbox_unit: "percent",
        page_width: 612,
        page_height: 792,
        page_rotation: 90,
        element_ids: ["tbl-1", "caption-1"],
        table_id: "tbl-1",
        table_cell_ref: "B2",
        formula_cell_ref: "B2",
      },
    } satisfies RetrievedChunk);

    expect(url).toBe(
      "/documents/doc-1?chunk_id=doc-1%3A7&page=3&bbox=10%2C20%2C70%2C45&bbox_mode=xyxy&bbox_unit=percent&page_width=612&page_height=792&page_rotation=90&element_id=tbl-1&table_id=tbl-1&cell_ref=B2&formula_cell_ref=B2"
    );
  });

  it("複数 cell metadata から先頭の formula cell を deep link に保持する", () => {
    const url = citationPreviewUrl({
      document_id: "doc-2",
      chunk_id: "doc-2:4",
      text: "集計セル",
      score: 0.88,
      rerank_score: null,
      file_name: "budget.xlsx",
      category_name: null,
      metadata: {
        table_id: "sheet-1-table-1",
        table_cell_refs: "A1,B2",
        formula_cell_refs: [" C3 ", "D4"],
      },
    } satisfies RetrievedChunk);

    expect(url).toBe(
      "/documents/doc-2?chunk_id=doc-2%3A4&table_id=sheet-1-table-1&cell_ref=C3&formula_cell_ref=C3"
    );
  });

  it("table_id がない adapter citation でも formula cell locator を deep link に保持する", () => {
    const url = citationPreviewUrl({
      document_id: "doc-3",
      chunk_id: "doc-3:9",
      text: "計算セル",
      score: 0.82,
      rerank_score: null,
      file_name: "sheet.xlsx",
      category_name: null,
      metadata: {
        page_start: 1,
        element_ids: "tbl-unknown",
        formula_cell_ref: "D4",
      },
    } satisfies RetrievedChunk);

    expect(url).toBe(
      "/documents/doc-3?chunk_id=doc-3%3A9&page=1&element_id=tbl-unknown&cell_ref=D4&formula_cell_ref=D4"
    );
  });

  it("object / JSON 形式の adapter cell metadata から schema remap 証跡を deep link に保持する", () => {
    const url = citationPreviewUrl({
      document_id: "doc-4",
      chunk_id: "doc-4:11",
      text: "表セル",
      score: 0.8,
      rerank_score: null,
      file_name: "adapter-table.pdf",
      category_name: null,
      metadata: {
        table_id: { table_id: "tbl-adapter-1" },
        table_cell_refs: [{ cell_ref: "A1" }],
        formula_cell_refs: '[{"formula_cell_ref":" B2 "}]',
      },
    } satisfies RetrievedChunk);

    expect(url).toBe(
      "/documents/doc-4?chunk_id=doc-4%3A11&table_id=tbl-adapter-1&cell_ref=B2&formula_cell_ref=B2"
    );
  });

  it("nested adapter cell metadata は table id ではなく cell ref を deep link に使う", () => {
    const url = citationPreviewUrl({
      document_id: "doc-5",
      chunk_id: "doc-5:12",
      text: "入れ子 metadata の表セル",
      score: 0.79,
      rerank_score: null,
      file_name: "adapter-nested-table.pdf",
      category_name: null,
      metadata: {
        table_id: { table_id: "tbl-nested-1" },
        formula_cell_refs:
          '{"table_id":"tbl-nested-1","cells":[{"metadata":{"formula_cell_ref":" E9 "}}]}',
      },
    } satisfies RetrievedChunk);

    expect(url).toBe(
      "/documents/doc-5?chunk_id=doc-5%3A12&table_id=tbl-nested-1&cell_ref=E9&formula_cell_ref=E9"
    );
  });
});
