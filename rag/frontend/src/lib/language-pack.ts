/**
 * 言語資源の画面の値の変換と検証（#2181）。
 *
 * 画面は項目ごとに 1 行 1 つの文字列（選択は値そのもの）で持ち、保存は組み込みと違う項目だけを送る。
 * 検証の規則は backend（`rag_parser_core.language`）と同じ。
 */
import type { LanguagePackField, LanguagePackSections, LanguagePackValue } from "./api";
import { t, type I18nKey } from "./i18n";

export type LanguagePackTexts = Record<string, string>;

export function fieldId(field: Pick<LanguagePackField, "section" | "key">): string {
  return `language-${field.section}-${field.key}`.replaceAll("_", "-");
}

export function textKey(field: Pick<LanguagePackField, "section" | "key">): string {
  return `${field.section}.${field.key}`;
}

function toText(value: LanguagePackValue | undefined): string {
  return Array.isArray(value) ? value.join("\n") : (value ?? "");
}

function toValue(field: LanguagePackField, text: string): LanguagePackValue {
  if (field.kind === "choice" || field.kind === "text") return text.trim();
  return [...new Set(text.split("\n").map((item) => item.trim()).filter(Boolean))];
}

export function textsFromSections(fields: LanguagePackField[], sections: LanguagePackSections): LanguagePackTexts {
  return Object.fromEntries(fields.map((field) => [textKey(field), toText(sections[field.section]?.[field.key])]));
}

/** 取込の項目（変えたら文書の分割し直しが要る）を、保存した値から変えたか。 */
export function changesIngestFields(
  fields: LanguagePackField[],
  texts: LanguagePackTexts,
  saved: LanguagePackTexts,
): boolean {
  return fields.some((field) => field.ingest && texts[textKey(field)] !== saved[textKey(field)]);
}

function sameValue(left: LanguagePackValue | undefined, right: LanguagePackValue | undefined): boolean {
  return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
}

/** 組み込みと違う項目だけの上書き（保存の形）。 */
export function overridesFromTexts(
  fields: LanguagePackField[],
  texts: LanguagePackTexts,
  builtin: LanguagePackSections,
): LanguagePackSections {
  const overrides: LanguagePackSections = {};
  for (const field of fields) {
    const value = toValue(field, texts[textKey(field)] ?? "");
    if (sameValue(value, builtin[field.section]?.[field.key])) continue;
    (overrides[field.section] ??= {})[field.key] = value;
  }
  return overrides;
}

/** 組み込みと違うか（「既定から変更」の表示と「既定に戻す」）。 */
export function isChanged(field: LanguagePackField, texts: LanguagePackTexts, builtin: LanguagePackSections) {
  return !sameValue(toValue(field, texts[textKey(field)] ?? ""), builtin[field.section]?.[field.key]);
}

const MAX_ITEMS = 300;

function itemError(field: LanguagePackField, item: string): I18nKey | null {
  const at = item.indexOf("=");
  const left = at < 0 ? "" : item.slice(0, at).trim();
  const right = at < 0 ? "" : item.slice(at + 1).trim();
  const chars = [...item];
  switch (field.kind) {
    case "pairs":
      return chars.length === 2 ? null : "settings.language.error.pair";
    case "number_patterns":
      return item.includes("N") ? null : "settings.language.error.numberPattern";
    case "char_values":
      return at > 0 && [...left].length === 1 && /^\d+$/.test(right) ? null : "settings.language.error.charValue";
    case "word_map":
      return left && right ? null : "settings.language.error.wordMap";
    case "enum_map":
      return left && field.choices.includes(right) ? null : "settings.language.error.enumMap";
    case "char_ranges":
      return chars.length === 1 || (chars.length === 3 && chars[1] === "-" && chars[0] <= chars[2])
        ? null
        : "settings.language.error.charRange";
    default:
      return null;
  }
}

/** 項目ごとの形の誤り（保存の前に欄の直下に出す）。 */
export function languagePackErrors(fields: LanguagePackField[], texts: LanguagePackTexts): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const key = textKey(field);
    const value = toValue(field, texts[key] ?? "");
    if (field.kind === "choice") {
      if (!field.choices.includes(value as string)) errors[key] = t("settings.language.error.choice");
      continue;
    }
    if (field.kind === "text") {
      const text = value as string;
      if (!text) errors[key] = t("settings.language.error.textRequired");
      else if ([...text].length > field.max_chars) errors[key] = t("settings.language.error.tooLong", { max: field.max_chars });
      continue;
    }
    const items = value as string[];
    if (items.length > MAX_ITEMS) {
      errors[key] = t("settings.language.error.tooMany", { max: MAX_ITEMS });
      continue;
    }
    if (items.some((item) => [...item].length > field.max_chars)) {
      errors[key] = t("settings.language.error.tooLong", { max: field.max_chars });
      continue;
    }
    const message = items.map((item) => itemError(field, item)).find(Boolean);
    if (message) errors[key] = field.kind === "enum_map" ? t(message, { choices: field.choices.join("・") }) : t(message);
  }
  return errors;
}
