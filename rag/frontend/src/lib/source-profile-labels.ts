import type { SourceModality, SourcePreviewKind } from "./api";
import { t, type I18nKey } from "./i18n";

const PARSER_PROFILE_KEYS: Record<string, I18nKey> = {
  enterprise_ai_pdf_layout: "sourceProfile.parser.pdf",
  enterprise_ai_image_ocr: "sourceProfile.parser.image",
  enterprise_ai_text_structure: "sourceProfile.parser.text",
  local_text_structure: "sourceProfile.parser.text",
  enterprise_ai_office_structure: "sourceProfile.parser.office",
  local_office_structure: "sourceProfile.parser.office",
  local_html_semantic: "sourceProfile.parser.html",
  local_email_thread: "sourceProfile.parser.email",
  unsupported_audio: "sourceProfile.parser.audio",
  unsupported_outlook_msg: "sourceProfile.parser.outlookMsg",
  unsupported_tiff_image: "sourceProfile.parser.tiffImage",
  unsupported_legacy_office_binary: "sourceProfile.parser.legacyOffice",
  enterprise_ai_generic: "sourceProfile.parser.generic",
  legacy: "evaluation.ingestionQuality.parser.legacy",
};

const SOURCE_WARNING_KEYS: Record<string, I18nKey> = {
  duplicate_content: "sourceProfile.warning.duplicate",
  content_type_missing: "sourceProfile.warning.contentTypeMissing",
  content_type_extension_mismatch: "sourceProfile.warning.contentTypeMismatch",
  large_file: "sourceProfile.warning.largeFile",
  unknown_modality: "sourceProfile.warning.unknown",
  unsupported_audio: "sourceProfile.warning.unsupportedAudio",
  unsupported_outlook_msg: "sourceProfile.warning.unsupportedOutlookMsg",
  unsupported_tiff_image: "sourceProfile.warning.unsupportedTiffImage",
  unsupported_legacy_office_binary: "sourceProfile.warning.unsupportedLegacyOffice",
  parser_fallback_used: "sourceProfile.warning.parserFallback",
  office_local_parse_failed: "sourceProfile.warning.officeFallback",
  office_segment_parse_failed: "sourceProfile.warning.officeSegmentFailed",
  failed_segments: "sourceProfile.warning.failedSegments",
  formula_review: "sourceProfile.warning.formulaReview",
  low_confidence_elements: "sourceProfile.warning.lowConfidenceElements",
};

const UNSUPPORTED_REASON_KEYS: Record<string, I18nKey> = {
  audio_transcription_not_configured: "sourceProfile.unsupported.audioTranscriptionNotConfigured",
  outlook_msg_not_supported: "sourceProfile.unsupported.outlookMsgNotSupported",
  tiff_image_not_supported: "sourceProfile.unsupported.tiffImageNotSupported",
  legacy_office_binary_not_supported: "sourceProfile.unsupported.legacyOfficeNotSupported",
  unknown_file_type: "sourceProfile.unsupported.unknownFileType",
};

const EVALUATION_WARNING_KEYS: Record<string, I18nKey> = {
  table_structure_review: "evaluation.ingestionQuality.warning.table",
  figure_ocr_review: "evaluation.ingestionQuality.warning.figure",
  long_document: "evaluation.ingestionQuality.warning.longDocument",
  low_extraction_confidence: "evaluation.ingestionQuality.warning.lowConfidence",
  no_structured_elements: "evaluation.ingestionQuality.warning.noElements",
  content_type_missing: "evaluation.ingestionQuality.warning.contentTypeMissing",
  content_type_extension_mismatch: "evaluation.ingestionQuality.warning.contentTypeMismatch",
  unknown_modality: "evaluation.ingestionQuality.warning.unknownModality",
  duplicate_content: "evaluation.ingestionQuality.warning.duplicate",
  large_file: "evaluation.ingestionQuality.warning.largeFile",
  parser_fallback_used: "evaluation.ingestionQuality.warning.parserFallback",
  failed_segments: "evaluation.ingestionQuality.warning.failedSegments",
  segment_extraction_artifact_cache_miss:
    "evaluation.ingestionQuality.warning.segmentArtifactCacheMiss",
  unsupported_audio: "evaluation.ingestionQuality.warning.unsupportedAudio",
  unsupported_outlook_msg: "evaluation.ingestionQuality.warning.unsupportedOutlookMsg",
  unsupported_tiff_image: "evaluation.ingestionQuality.warning.unsupportedTiffImage",
  unsupported_legacy_office_binary: "evaluation.ingestionQuality.warning.unsupportedLegacyOffice",
};

export function parserProfileKey(profile: string): I18nKey {
  return PARSER_PROFILE_KEYS[profile] ?? "sourceProfile.parser.generic";
}

export function sourceModalityKey(modality: SourceModality): I18nKey {
  return `sourceProfile.modality.${modality}` as I18nKey;
}

export function sourcePreviewKey(kind: SourcePreviewKind): I18nKey {
  return `sourceProfile.preview.${kind}` as I18nKey;
}

export function sourceWarningKey(warning: string): I18nKey {
  return SOURCE_WARNING_KEYS[warning] ?? "sourceProfile.warning.generic";
}

export function unsupportedReasonLabel(reason: string | null | undefined): string {
  if (!reason) return "";
  const key = UNSUPPORTED_REASON_KEYS[reason];
  return key ? t(key) : reason;
}

export function qualityCodeLabel(value: string): string {
  const warningKey = EVALUATION_WARNING_KEYS[value] ?? SOURCE_WARNING_KEYS[value];
  if (warningKey) return t(warningKey);
  if (value in PARSER_PROFILE_KEYS) return t(parserProfileKey(value));
  return value;
}
