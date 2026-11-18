import type { LanguagePackField, LanguagePackSections } from "./api";

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

/**
 * プロファイルの上書きの土台: 全体の値のうち、根拠がないときの固定返信の 2 項目だけを組み込みの値にする（#5463。
 * 固定返信は全体では設定せず、プロファイルの上書きか組み込みの値だけを使う。backend の PROFILE_ONLY_LANGUAGE_PACK_FIELDS）。
 */
export function withBuiltinEmptyEvidenceResponse(effective: LanguagePackSections, builtin: LanguagePackSections): LanguagePackSections {
  const labels = { ...effective.answer_labels };
  for (const key of EMPTY_EVIDENCE_RESPONSE_KEYS) {
    if (builtin.answer_labels && key in builtin.answer_labels) labels[key] = builtin.answer_labels[key];
  }
  return { ...effective, answer_labels: labels };
}

/** 保存済み実効値に現在の payload を重ねる。未編集の値を再度フロントの trim に通さない。 */
export function effectiveEmptyEvidenceResponsePreview(baseline: LanguagePackSections, overrides: LanguagePackSections = {}): string {
  return EMPTY_EVIDENCE_RESPONSE_KEYS.map((key) => backendStrip(String(
    overrides.answer_labels?.[key] ?? baseline.answer_labels?.[key] ?? "",
  ))).join("");
}
