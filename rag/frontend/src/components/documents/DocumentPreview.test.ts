import { describe, expect, it } from "vitest";

import { pdfPreviewUrl } from "./DocumentPreview";

describe("pdfPreviewUrl", () => {
  it("PDF 原本プレビューではナビゲーションペインを初期表示しない", () => {
    expect(pdfPreviewUrl("/api/documents/doc-1/content")).toBe(
      "/api/documents/doc-1/content#pagemode=none&navpanes=0"
    );
  });

  it("ページ指定とナビゲーションペイン非表示を同時に保持する", () => {
    expect(pdfPreviewUrl("/api/documents/doc-1/content", 11)).toBe(
      "/api/documents/doc-1/content#page=11&pagemode=none&navpanes=0"
    );
  });
});
