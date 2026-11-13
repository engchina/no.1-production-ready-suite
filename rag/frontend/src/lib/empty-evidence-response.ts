import type { LanguagePackField } from "./api";
import type { LanguagePackTexts } from "./language-pack";

export const EMPTY_EVIDENCE_RESPONSE_KEYS = [
  "insufficient_evidence_summary",
  "empty_search_guidance",
] as const;

export function isEmptyEvidenceResponseField(field: Pick<LanguagePackField, "section" | "key">): boolean {
  return field.section === "answer_labels" && EMPTY_EVIDENCE_RESPONSE_KEYS.some((key) => key === field.key);
}

/** フロントの保存用 trim の後、backend の Python str.strip と同じ順序で除去する。 */
function effectiveSavedText(text: string): string {
  const characters = [...text.trim()];
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
  return EMPTY_EVIDENCE_RESPONSE_KEYS.map((key) => effectiveSavedText(texts[`answer_labels.${key}`] ?? "")).join("");
}
