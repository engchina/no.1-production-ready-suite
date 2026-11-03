import { describe, expect, it } from "vitest";

import { EXTRACTION_EXPORT_FORMATS } from "./DocumentExtractionExport.logic";

// コピーの書き込み（取得を待つ前に ClipboardItem へ渡す Safari の対応）は共有 UI の copyTextToClipboard が持つ
// （platform/packages/ui/tests/clipboard.test.ts。#2099）。
describe("EXTRACTION_EXPORT_FORMATS", () => {
  it("Markdown / HTML の後に抽出結果と Chunk の JSON を区別して並べる", () => {
    expect(EXTRACTION_EXPORT_FORMATS.map((item) => item.format)).toEqual([
      "markdown",
      "html",
      "json",
      "chunks",
    ]);
    expect(EXTRACTION_EXPORT_FORMATS.map((item) => item.extension)).toEqual([
      ".md",
      ".html",
      ".json",
      ".json",
    ]);
  });
});
