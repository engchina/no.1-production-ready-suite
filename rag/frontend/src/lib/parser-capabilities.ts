// parser backend × 対応ファイル形式の表示・判定ヘルパー。
// 宣言の正本は backend の rag_parser_core.capabilities(GET /settings/parser-adapters の
// capabilities で配信)。frontend には複製を持たない。

import type { ParserBackendCapabilityData } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

// preprocess 変換で parser へ渡る実効 modality が変わるもののみ列挙する。
// 未知の組合せは「判定しない」(null)へ倒し、誤警告を出さない。
const PREPROCESS_MODALITY_TRANSFORMS: Record<string, { from: string; to: string }> = {
  office_to_pdf: { from: "office", to: "pdf" },
  pdf_to_page_images: { from: "pdf", to: "image" },
  excel_to_json: { from: "office", to: "text" },
  url_to_markdown: { from: "html", to: "text" },
};

export function findParserCapability(
  capabilities: readonly ParserBackendCapabilityData[] | undefined,
  backend: string | null | undefined
): ParserBackendCapabilityData | null {
  if (!capabilities || !backend) return null;
  return capabilities.find((item) => item.backend === backend) ?? null;
}

/** 「PDF・画像」形式の日本語ラベル。宣言なし/空は空文字。 */
export function formatSupportedFormats(
  capability: ParserBackendCapabilityData | null
): string {
  if (!capability || capability.modalities.length === 0) return "";
  return capability.modalities
    .map((modality) => t(`sourceProfile.modality.${modality}` as I18nKey))
    .join("・");
}

/** 拡張子一覧の表示文字列(「.pdf .md …」)。宣言なしは空文字。 */
export function formatSupportedExtensions(
  capability: ParserBackendCapabilityData | null
): string {
  if (!capability || capability.extensions.length === 0) return "";
  return [...capability.extensions].sort().join(" ");
}

/** preprocess 変換を考慮した実効 modality。 */
export function effectiveModalityForParser(
  modality: string,
  preprocessProfile: string | null | undefined
): string {
  const transform = preprocessProfile
    ? PREPROCESS_MODALITY_TRANSFORMS[preprocessProfile]
    : undefined;
  if (transform && transform.from === modality) return transform.to;
  return modality;
}

/**
 * backend が文書の modality を処理できるか。
 * 判定材料が足りない(宣言なし・unknown/audio modality)場合は null = 判定しない。
 */
export function parserSupportsDocument({
  capabilities,
  backend,
  modality,
  preprocessProfile,
}: {
  capabilities: readonly ParserBackendCapabilityData[] | undefined;
  backend: string | null | undefined;
  modality: string | null | undefined;
  preprocessProfile?: string | null;
}): boolean | null {
  const capability = findParserCapability(capabilities, backend);
  if (!capability || !modality) return null;
  const effective = effectiveModalityForParser(modality, preprocessProfile);
  if (effective === "unknown" || effective === "audio") return null;
  return capability.modalities.includes(effective);
}
