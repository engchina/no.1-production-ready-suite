import { describe, expect, it } from "vitest";

import type { ParserBackendCapabilityData } from "@/lib/api";
import {
  effectiveModalityForParser,
  findParserCapability,
  formatSupportedExtensions,
  formatSupportedFormats,
  parserSupportsDocument,
} from "@/lib/parser-capabilities";

const CAPABILITIES: ParserBackendCapabilityData[] = [
  { backend: "marker", modalities: ["pdf", "image"], extensions: [".pdf", ".png"] },
  {
    backend: "unstructured",
    modalities: ["pdf", "image", "text", "html", "email", "office"],
    extensions: [],
  },
];

describe("formatSupportedFormats", () => {
  it("modality を日本語ラベルで「・」連結する", () => {
    expect(formatSupportedFormats(findParserCapability(CAPABILITIES, "marker"))).toBe(
      "PDF・画像"
    );
  });

  it("宣言なしは空文字", () => {
    expect(formatSupportedFormats(null)).toBe("");
    expect(formatSupportedFormats(findParserCapability(CAPABILITIES, "no_such"))).toBe("");
  });
});

describe("effectiveModalityForParser", () => {
  it("office_to_pdf は office を pdf として扱う", () => {
    expect(effectiveModalityForParser("office", "office_to_pdf")).toBe("pdf");
  });

  it("変換対象外の modality はそのまま", () => {
    expect(effectiveModalityForParser("pdf", "office_to_pdf")).toBe("pdf");
    expect(effectiveModalityForParser("office", null)).toBe("office");
  });

  it("pdf_to_page_images は pdf を image として扱う", () => {
    expect(effectiveModalityForParser("pdf", "pdf_to_page_images")).toBe("image");
  });
});

describe("parserSupportsDocument", () => {
  it("marker × office は非対応", () => {
    expect(
      parserSupportsDocument({
        capabilities: CAPABILITIES,
        backend: "marker",
        modality: "office",
      })
    ).toBe(false);
  });

  it("marker × office でも office_to_pdf 変換があれば対応", () => {
    expect(
      parserSupportsDocument({
        capabilities: CAPABILITIES,
        backend: "marker",
        modality: "office",
        preprocessProfile: "office_to_pdf",
      })
    ).toBe(true);
  });

  it("判定材料不足(宣言なし/未取得/unknown)は null", () => {
    expect(
      parserSupportsDocument({
        capabilities: CAPABILITIES,
        backend: "no_such",
        modality: "pdf",
      })
    ).toBeNull();
    expect(
      parserSupportsDocument({ capabilities: undefined, backend: "marker", modality: "pdf" })
    ).toBeNull();
    expect(
      parserSupportsDocument({
        capabilities: CAPABILITIES,
        backend: "marker",
        modality: "unknown",
      })
    ).toBeNull();
  });
});

describe("formatSupportedExtensions", () => {
  it("拡張子をソートして空白連結する", () => {
    expect(
      formatSupportedExtensions({
        backend: "unstructured",
        modalities: ["text"],
        extensions: [".md", ".csv", ".pdf"],
      })
    ).toBe(".csv .md .pdf");
  });

  it("宣言なしは空文字", () => {
    expect(formatSupportedExtensions(null)).toBe("");
    expect(
      formatSupportedExtensions({ backend: "x", modalities: [], extensions: [] })
    ).toBe("");
  });
});
