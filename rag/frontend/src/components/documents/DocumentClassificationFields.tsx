import { FormGrid, TextField } from "@production-ready/ui";

import { CategorySelectFields } from "./CategorySelectFields";
import type { DocumentClassification } from "@/lib/api";
import { t } from "@/lib/i18n";

export const EMPTY_CLASSIFICATION: DocumentClassification = {
  large_category: null,
  middle_category: null,
  small_category: null,
  effective_from: null,
  effective_to: null,
};
const CLASSIFICATION_DATE_FIELDS = ["effective_from", "effective_to"] as const;

/** 終了日の欄の id（期間の誤りのときにフォーカスを移す）。 */
export function classificationEffectiveToId(idPrefix: string): string {
  return `${idPrefix}-effective_to`;
}

/**
 * 文書の分類（大・中・小）と有効期間の入力欄。文書の詳細とアップロードで共通に使う（#5464）。
 * 分類は「文書の分類」の一覧から選ぶ（必須ではない。#2131）。名前の段（md）、有効期間の開始日・終了日は
 * 日付の段（sm）のペア（#1891）。期間の誤りは終了日の欄の直下に出す（#944）。
 */
export function DocumentClassificationFields({
  idPrefix,
  value,
  onChange,
  periodError,
  disabled,
}: {
  idPrefix: string;
  value: DocumentClassification;
  onChange: (value: DocumentClassification) => void;
  periodError?: string | null;
  disabled?: boolean;
}) {
  return (
    <>
      <FormGrid>
        <CategorySelectFields
          idPrefix={idPrefix}
          labels={{
            large_category: t("documents.classification.large_category"),
            middle_category: t("documents.classification.middle_category"),
            small_category: t("documents.classification.small_category"),
          }}
          values={{
            large_category: value.large_category ?? "",
            middle_category: value.middle_category ?? "",
            small_category: value.small_category ?? "",
          }}
          disabled={disabled}
          onChange={(values) =>
            onChange({
              ...value,
              large_category: values.large_category || null,
              middle_category: values.middle_category || null,
              small_category: values.small_category || null,
            })
          }
        />
      </FormGrid>
      <FormGrid>
        {CLASSIFICATION_DATE_FIELDS.map((key) => (
          <TextField
            key={key}
            id={`${idPrefix}-${key}`}
            width="sm"
            type="date"
            label={t(`documents.classification.${key}`)}
            helper={key === "effective_to" ? t("documents.classification.effectiveToHelper") : undefined}
            error={key === "effective_to" ? (periodError ?? undefined) : undefined}
            value={value[key] ?? ""}
            disabled={disabled}
            onValueChange={(next) => onChange({ ...value, [key]: next || null })}
          />
        ))}
      </FormGrid>
    </>
  );
}

/** 閉じた見出しの下に出す分類と有効期間（無ければ「未設定」）。 */
export function classificationSummary(value: DocumentClassification): string {
  const categories = [value.large_category, value.middle_category, value.small_category].filter(Boolean).join(" / ");
  const period =
    value.effective_from || value.effective_to
      ? t("documents.classification.periodSummary", { from: value.effective_from ?? "", to: value.effective_to ?? "" })
      : "";
  return [categories, period].filter(Boolean).join("・") || t("documents.classification.unset");
}
