import { FormGrid, resolveContentLang } from "@production-ready/ui";

import { LanguageField, languagePackFieldId } from "./LanguagePackFieldsPanel";
import type { LanguagePackField, LanguagePackSections } from "@/lib/api";
import { EMPTY_EVIDENCE_RESPONSE_KEYS, effectiveEmptyEvidenceResponsePreview, isEmptyEvidenceResponseField } from "@/lib/empty-evidence-response";
import { t } from "@/lib/i18n";
import type { LanguagePackTexts } from "@/lib/language-pack";

/**
 * 検索・回答プロファイルの、根拠がないときの基本本文の編集と確認。全体では設定せず、上書きしない項目は組み込みの値を
 * 使う（#5463）。同じ画面の「窓口」の区画と同じ枠に入れる。
 */
export function EmptyEvidenceResponseSettings({
  idPrefix, fields, baseline, texts, errors, disabled, profileOverrides, onChange, onReset,
}: {
  idPrefix: string;
  fields: LanguagePackField[];
  /** 固定返信の 2 項目は組み込みの値（`withBuiltinEmptyEvidenceResponse`）。 */
  baseline: LanguagePackSections;
  texts: LanguagePackTexts;
  errors: Record<string, string>;
  disabled: boolean;
  profileOverrides: LanguagePackSections;
  onChange: (key: string, value: string) => void;
  onReset: (key: string) => void;
}) {
  const id = `${idPrefix}-empty-evidence-response`;
  const preview = effectiveEmptyEvidenceResponsePreview(baseline, profileOverrides);
  return (
    <fieldset className="space-y-3 rounded-lg border border-border p-4" data-testid="empty-evidence-response-settings">
      <legend className="px-1 text-sm font-semibold text-fg">{t("settings.language.emptyEvidence.title")}</legend>
      <p className="text-xs text-fg-muted">{t("settings.language.emptyEvidence.helper")}</p>
      <p className="text-xs text-fg-muted">{t("settings.language.emptyEvidence.profileSave")}</p>
      <div className="space-y-4 rounded-lg border border-border bg-surface-sunken p-3">
        <FormGrid>
          {fields.filter(isEmptyEvidenceResponseField).sort((left, right) =>
            EMPTY_EVIDENCE_RESPONSE_KEYS.findIndex((key) => key === left.key) - EMPTY_EVIDENCE_RESPONSE_KEYS.findIndex((key) => key === right.key),
          ).map((field) => {
            const key = `${field.section}.${field.key}`;
            return <LanguageField
              key={key}
              id={languagePackFieldId(idPrefix, field)}
              field={field}
              baseline={baseline}
              texts={texts}
              error={errors[key]}
              disabled={disabled}
              overridden={Object.hasOwn(profileOverrides[field.section] ?? {}, field.key)}
              changedLabel={t("settings.language.emptyEvidence.overridden")}
              unchangedLabel={t("settings.language.emptyEvidence.builtin")}
              resetLabel={t("settings.language.emptyEvidence.reset")}
              onChange={(value) => onChange(key, value)}
              onReset={() => onReset(key)}
            />;
          })}
        </FormGrid>
        <div className="space-y-2" role="region" aria-labelledby={`${id}-preview-title`}>
          <h4 id={`${id}-preview-title`} className="text-sm font-semibold text-fg">{t("settings.language.emptyEvidence.preview")}</h4>
          <p className="text-xs text-fg-muted">{t("settings.language.emptyEvidence.previewHelp")}</p>
          <p lang={resolveContentLang(undefined, preview)} className="whitespace-pre-wrap break-words text-sm text-fg" data-testid="empty-evidence-response-preview">{preview}</p>
        </div>
      </div>
    </fieldset>
  );
}
