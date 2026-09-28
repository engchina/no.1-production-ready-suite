import { describe, expect, it } from "vitest";

import {
  isSameParserBackend,
  parserProfileKey,
  parserBackendLabel,
  qualityCodeLabel,
  sourceWarningKey,
  unsupportedReasonLabel,
} from "./source-profile-labels";
import { t } from "./i18n";

describe("source profile labels", () => {
  it("unsupported parser profiles use specific Japanese i18n keys", () => {
    expect(t(parserProfileKey("unsupported_outlook_msg"))).toBe("Outlook MSG 未対応");
    expect(t(parserProfileKey("unsupported_tiff_image"))).toBe("TIFF 画像未対応");
    expect(t(parserProfileKey("unsupported_legacy_office_binary"))).toBe("旧 Office 未対応");
  });

  it("unsupported warnings avoid falling back to generic copy", () => {
    expect(t(sourceWarningKey("unsupported_outlook_msg"))).toContain("Outlook MSG");
    expect(t(sourceWarningKey("unsupported_tiff_image"))).toContain("TIFF");
    expect(t(sourceWarningKey("unsupported_legacy_office_binary"))).toContain("旧形式");
  });

  it("unsupported reasons are presented as user-facing Japanese messages", () => {
    expect(unsupportedReasonLabel("tiff_image_not_supported")).toContain("PNG/JPEG/WEBP");
    expect(unsupportedReasonLabel("legacy_office_binary_not_supported")).toContain(
      "DOCX/PPTX/XLSX"
    );
    expect(unsupportedReasonLabel("outlook_msg_not_supported")).toContain("EML");
    expect(unsupportedReasonLabel("custom_reason")).toBe("custom_reason");
  });

  it("evaluation quality labels share the same unsupported warning mapping", () => {
    expect(qualityCodeLabel("unsupported_tiff_image")).toBe("TIFF 画像未対応");
    expect(qualityCodeLabel("unsupported_legacy_office_binary")).toBe("旧 Office 未対応");
  });

  it("evaluation quality labels expose segment artifact cache misses in Japanese", () => {
    expect(qualityCodeLabel("segment_extraction_artifact_cache_miss")).toBe(
      "Segment artifact 再抽出"
    );
  });

  it("parser backend labels hide external adapter implementation names", () => {
    expect(parserBackendLabel("dots_ocr_adapter")).toBe("Dots.OCR");
    expect(isSameParserBackend("dots_ocr_adapter", "dots_ocr")).toBe(true);
    expect(parserBackendLabel("mineru_adapter")).toBe("MinerU");
    expect(isSameParserBackend("mineru_adapter", "mineru")).toBe(true);
  });

  it("削除済みの解析エンジン名は過去の解析結果でも生の値のまま表示する", () => {
    // Marker / Unlimited-OCR / GLM-OCR は #270 で削除した。過去の解析結果に残る名前は壊さず表示する。
    expect(parserBackendLabel("marker_adapter")).toBe("marker_adapter");
    expect(parserBackendLabel("unlimited_ocr_adapter")).toBe("unlimited_ocr_adapter");
    expect(parserBackendLabel("glm_ocr")).toBe("glm_ocr");
  });
});
