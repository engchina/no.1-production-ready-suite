"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Disclosure,
  Fieldset,
  FormGrid,
  TextareaField,
} from "@production-ready/ui";
import { BookOpenText, Link2 } from "lucide-react";
import type { ReferenceVocabulary } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

/** 交差参照の解析の語の項目（#2157）。画面では 1 行 1 語の文字列で持つ。 */
export type ReferenceVocabularyField = keyof ReferenceVocabulary;
export type ReferenceTexts = Record<ReferenceVocabularyField, string>;

/** 文書名の見分け方の項目と、条・部の参照の書き方の項目（画面の並び）。 */
const DOCUMENT_FIELDS: ReferenceVocabularyField[] = [
  "document_name_predicates",
  "document_suffixes",
  "numbered_document_suffixes",
  "excluded_name_endings",
  "generic_names",
  "demonstrative_words",
  "self_words",
  "edition_words",
  "era_names",
  "company_suffixes",
];
const ARTICLE_FIELDS: ReferenceVocabularyField[] = [
  "part_prefixes",
  "separate_part_patterns",
  "item_patterns",
  "appendix_words",
  "branch_connectors",
  "previous_words",
  "next_words",
  "same_words",
];
/** 参照の手がかりと列のつなぎの項目（#2171）。 */
const CUE_FIELDS: ReferenceVocabularyField[] = ["cue_words", "connector_words"];
/** 略称の参照先の項目（派生の文書の「法第N条」を本体の文書の参照にする。#2169）。 */
const ABBREVIATION_FIELDS: ReferenceVocabularyField[] = ["derived_document_suffixes", "abbreviation_targets"];
export const REFERENCE_VOCABULARY_FIELDS: ReferenceVocabularyField[] = [
  ...CUE_FIELDS,
  ...DOCUMENT_FIELDS,
  ...ARTICLE_FIELDS,
  ...ABBREVIATION_FIELDS,
];

/** 語の一覧が長い項目は欄を高くする（一般語・語尾の一覧）。 */
const LONG_FIELDS = new Set<ReferenceVocabularyField>([
  "document_name_predicates",
  "appendix_words",
  "cue_words",
  "connector_words",
  "document_suffixes",
  "excluded_name_endings",
  "generic_names",
]);

export function referenceTextsFromVocabulary(vocabulary: ReferenceVocabulary): ReferenceTexts {
  return Object.fromEntries(
    REFERENCE_VOCABULARY_FIELDS.map((field) => [field, (vocabulary[field] ?? []).join("\n")]),
  ) as ReferenceTexts;
}

/** 1 行 1 語の文字列を、前後の空白・空行・重複を除いた語の一覧にする。 */
export function referenceVocabularyFromTexts(texts: ReferenceTexts): ReferenceVocabulary {
  return Object.fromEntries(
    REFERENCE_VOCABULARY_FIELDS.map((field) => [
      field,
      [...new Set((texts[field] ?? "").split("\n").map((word) => word.trim()).filter(Boolean))],
    ]),
  ) as unknown as ReferenceVocabulary;
}

/**
 * 番号の位置（N）が無い「番号の体系が別になる部の印」と、「略称=参照先の文書名」の形でない略称
 * （保存の前に欄の直下で知らせる。backend の検証と同じ規則）。
 */
export function referenceVocabularyErrors(texts: ReferenceTexts): Partial<Record<ReferenceVocabularyField, string>> {
  const vocabulary = referenceVocabularyFromTexts(texts);
  const errors: Partial<Record<ReferenceVocabularyField, string>> = {};
  if (vocabulary.separate_part_patterns.some((pattern) => !pattern.includes("N"))) {
    errors.separate_part_patterns = t("settings.chunking.reference.error.patternNumber");
  }
  const malformed = (item: string) => {
    const at = item.indexOf("=");
    return at <= 0 || !item.slice(0, at).trim() || !item.slice(at + 1).trim();
  };
  if (vocabulary.appendix_words.some(malformed)) {
    errors.appendix_words = t("settings.chunking.reference.error.appendixForm");
  }
  if (vocabulary.abbreviation_targets.some(malformed)) {
    errors.abbreviation_targets = t("settings.chunking.reference.error.abbreviationForm");
  }
  return errors;
}

export function referenceFieldId(field: ReferenceVocabularyField): string {
  return `chunking-reference-${field.replaceAll("_", "-")}`;
}

export function ReferenceVocabularyCard({
  texts,
  errors,
  saving,
  onChange,
}: {
  texts: ReferenceTexts;
  errors: Partial<Record<ReferenceVocabularyField, string>>;
  saving: boolean;
  onChange: (texts: ReferenceTexts) => void;
}) {
  const field = (name: ReferenceVocabularyField) => (
    <TextareaField
      key={name}
      id={referenceFieldId(name)}
      label={t(`settings.chunking.reference.${name}` as I18nKey)}
      helper={t(`settings.chunking.reference.${name}.help` as I18nKey)}
      error={errors[name]}
      value={texts[name]}
      rows={LONG_FIELDS.has(name) ? 6 : 3}
      width="md"
      disabled={saving}
      onValueChange={(value) => onChange({ ...texts, [name]: value })}
    />
  );
  // 欄にエラーがあれば開いて見せる（保存を押したときに最初のエラーの欄へ移す）。
  const hasError = REFERENCE_VOCABULARY_FIELDS.some((name) => errors[name]);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Link2 size={16} className="text-accent-fg" aria-hidden />
          {t("settings.chunking.reference.title")}
        </CardTitle>
        <CardDescription>{t("settings.chunking.reference.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <Disclosure
          summary={t("settings.chunking.reference.edit")}
          description={t("settings.chunking.reference.editDescription")}
          icon={BookOpenText}
          {...(hasError ? { open: true } : {})}
        >
          <div className="space-y-6">
            <Fieldset legend={t("settings.chunking.reference.cues")} helper={t("settings.chunking.reference.cues.help")}>
              <FormGrid>{CUE_FIELDS.map(field)}</FormGrid>
            </Fieldset>
            <Fieldset legend={t("settings.chunking.reference.documents")} helper={t("settings.chunking.reference.documents.help")}>
              <FormGrid>{DOCUMENT_FIELDS.map(field)}</FormGrid>
            </Fieldset>
            <Fieldset legend={t("settings.chunking.reference.articles")} helper={t("settings.chunking.reference.articles.help")}>
              <FormGrid>{ARTICLE_FIELDS.map(field)}</FormGrid>
            </Fieldset>
            <Fieldset
              legend={t("settings.chunking.reference.abbreviations")}
              helper={t("settings.chunking.reference.abbreviations.help")}
            >
              <FormGrid>{ABBREVIATION_FIELDS.map(field)}</FormGrid>
            </Fieldset>
          </div>
        </Disclosure>
      </CardContent>
    </Card>
  );
}
