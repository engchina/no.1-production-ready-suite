import { describe, expect, it } from "vitest";

import type { FileTypeDefaultEntry, FileTypeDefaultRow } from "@/lib/api";

import {
  draftsEqual,
  entryWithPreprocess,
  parserOptionsFor,
  updatePayload,
  withCurrentValue,
  type FileTypeDraft,
} from "./FileTypeDefaultsSettingsClient.logic";

const EMPTY: FileTypeDefaultEntry = {
  preprocess_profile: null,
  parser_adapter_backend: null,
  chunking_strategy: null,
};

function excelRow(overrides: Partial<FileTypeDefaultRow> = {}): FileTypeDefaultRow {
  return {
    ...EMPTY,
    file_type: "excel",
    extensions: [".xlsx", ".xls"],
    preprocess_options: ["passthrough", "excel_to_jsonl"],
    unavailable_preprocess_profiles: [],
    parser_options: {
      passthrough: ["unstructured"],
      excel_to_jsonl: ["structured_records_jsonl"],
    },
    unavailable_parser_backends: [],
    inherited_preprocess_profile: "passthrough",
    ...overrides,
  };
}

describe("形式別の既定の下書き", () => {
  it("未設定の行は全体の既定のファイル準備で解析エンジンを絞る", () => {
    expect(parserOptionsFor(excelRow(), null)).toEqual(["unstructured"]);
    expect(parserOptionsFor(excelRow({ inherited_preprocess_profile: "excel_to_jsonl" }), null)).toEqual([
      "structured_records_jsonl",
    ]);
  });

  it("構造化レコードの準備を選ぶと解析エンジンと固定分割を入れる", () => {
    const next = entryWithPreprocess(excelRow(), EMPTY, "excel_to_jsonl");
    expect(next).toEqual({
      preprocess_profile: "excel_to_jsonl",
      parser_adapter_backend: "structured_records_jsonl",
      chunking_strategy: "fixed_delimiter",
    });
  });

  it("利用者が選んだ分割方式は変えない", () => {
    const next = entryWithPreprocess(
      excelRow(),
      { ...EMPTY, chunking_strategy: "page_level" },
      "excel_to_jsonl"
    );
    expect(next.chunking_strategy).toBe("page_level");
  });

  it("選べなくなった解析エンジンは外す", () => {
    const row = excelRow();
    const current = { ...EMPTY, parser_adapter_backend: "structured_records_jsonl" } as const;
    expect(entryWithPreprocess(row, current, "passthrough").parser_adapter_backend).toBeNull();
    expect(
      entryWithPreprocess(row, { ...EMPTY, parser_adapter_backend: "unstructured" }, "passthrough")
        .parser_adapter_backend
    ).toBe("unstructured");
  });

  it("保存済みの値は選択肢に無くても表示する", () => {
    expect(withCurrentValue(["docling"], "dots_ocr")).toEqual(["docling", "dots_ocr"]);
    expect(withCurrentValue(["docling"], null)).toEqual(["docling"]);
  });

  it("保存の内容は設定のある行だけ", () => {
    const draft = { pdf: EMPTY, word: { ...EMPTY, chunking_strategy: "page_level" } } as FileTypeDraft;
    expect(updatePayload(draft)).toEqual({ entries: { word: draft.word } });
  });

  it("差分は行の値で判定する", () => {
    const left = { pdf: EMPTY } as FileTypeDraft;
    expect(draftsEqual(left, { pdf: { ...EMPTY } } as FileTypeDraft)).toBe(true);
    expect(draftsEqual(left, { pdf: { ...EMPTY, chunking_strategy: "fixed_size" } } as FileTypeDraft)).toBe(false);
  });
});
