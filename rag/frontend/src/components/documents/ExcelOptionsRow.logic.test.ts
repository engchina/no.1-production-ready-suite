import { describe, expect, it } from "vitest";

import { DEFAULT_EXCEL_OPTIONS, excelOptionsSummary, formatColumnRoles, parseColumnRoles } from "./ExcelOptionsRow";

// 列の役割の指定の書き方（#1281）。
describe("parseColumnRoles", () => {
  it("列名か列の記号と役割を読み、日本語と英語の役割の名前を受け付ける", () => {
    expect(parseColumnRoles("D=例示、設定値＝現在値, E=なし\nF=Default")).toEqual({
      roles: { D: "example", 設定値: "current", E: "none", F: "default" },
      invalid: [],
    });
  });

  it("読めない項目は保存せず invalid に返す", () => {
    expect(parseColumnRoles("D=実際の値、=既定値、C、B=既定値=例示、A=範囲")).toEqual({
      roles: { A: "allowed" },
      invalid: ["D=実際の値", "=既定値", "C", "B=既定値=例示"],
    });
  });

  it("書き戻すと同じ指定に読める", () => {
    const roles = { D: "example", 説明: "definition" } as const;
    expect(parseColumnRoles(formatColumnRoles(roles)).roles).toEqual(roles);
  });
});

describe("excelOptionsSummary", () => {
  it("判定しない設定と役割の指定を要約に出す", () => {
    const summary = excelOptionsSummary({
      ...DEFAULT_EXCEL_OPTIONS,
      column_role_detection: "off",
      column_roles: { D: "example" },
    });
    expect(summary).toContain("列の役割は判定しない");
    expect(summary).toContain("列の役割の指定: D=例示");
    expect(excelOptionsSummary(DEFAULT_EXCEL_OPTIONS)).not.toContain("列の役割");
  });
});
