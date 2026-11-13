import type { LanguagePackField, LanguagePackSections } from "./api";
import type { LanguagePackTexts } from "./language-pack";

export const EMPTY_EVIDENCE_RESPONSE_KEYS = [
  "insufficient_evidence_summary",
  "empty_search_guidance",
] as const;

export function isEmptyEvidenceResponseField(field: Pick<LanguagePackField, "section" | "key">): boolean {
  return field.section === "answer_labels" && EMPTY_EVIDENCE_RESPONSE_KEYS.some((key) => key === field.key);
}

/** backend の Python str.strip が除く境界の空白だけを扱う。 */
function backendStrip(text: string): string {
  const characters = [...text];
  const isWhitespace = (character: string): boolean => {
    const codePoint = character.codePointAt(0)!;
    return /^\p{White_Space}$/u.test(character) || (codePoint >= 0x1c && codePoint <= 0x1f);
  };
  let start = 0;
  let end = characters.length;
  while (start < end && isWhitespace(characters[start])) start += 1;
  while (end > start && isWhitespace(characters[end - 1])) end -= 1;
  return characters.slice(start, end).join("");
}

/** 保存後の実効値から、正常な根拠なし応答の本文を区切りなしで組み立てる。 */
export function emptyEvidenceResponsePreview(texts: LanguagePackTexts): string {
  return EMPTY_EVIDENCE_RESPONSE_KEYS.map((key) => backendStrip((texts[`answer_labels.${key}`] ?? "").trim())).join("");
}

/** 保存済み実効値に現在の payload を重ねる。未編集の値を再度フロントの trim に通さない。 */
export function effectiveEmptyEvidenceResponsePreview(baseline: LanguagePackSections, overrides: LanguagePackSections = {}): string {
  return EMPTY_EVIDENCE_RESPONSE_KEYS.map((key) => backendStrip(String(
    overrides.answer_labels?.[key] ?? baseline.answer_labels?.[key] ?? "",
  ))).join("");
}
