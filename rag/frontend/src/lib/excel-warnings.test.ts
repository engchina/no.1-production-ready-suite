import { describe, expect, it } from "vitest";

import { excelWarningLabel, lowConfidenceHeaderSheets } from "./excel-warnings";

describe("excelWarningLabel", () => {
  it("Excel の前処理の warning を日本語にする", () => {
    expect(excelWarningLabel("excel_header_low_confidence:費目")).toContain("シート「費目」の表頭");
    expect(excelWarningLabel("excel_formula_without_cached_value:集計:3")).toContain(
      "シート「集計」の数式 3 件"
    );
    expect(excelWarningLabel("excel_sheet_not_found:無い")).toContain("「無い」");
    expect(excelWarningLabel("excel_range_sheet_not_found:手順 1")).toContain("「手順 1」");
  });

  it("Excel の warning でなければ null", () => {
    expect(excelWarningLabel("office_to_pdf_failed")).toBeNull();
  });
});

describe("lowConfidenceHeaderSheets", () => {
  it("表頭の推定の信頼度が低いシートを重複なく返す", () => {
    expect(
      lowConfidenceHeaderSheets([
        "excel_header_low_confidence:費目",
        "table_structure_review",
        "excel_header_low_confidence:費目",
        "excel_header_low_confidence:時刻 10:00",
      ])
    ).toEqual(["費目", "時刻 10:00"]);
    expect(lowConfidenceHeaderSheets([])).toEqual([]);
  });
});
