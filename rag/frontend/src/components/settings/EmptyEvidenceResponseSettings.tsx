import { FormGrid, resolveContentLang } from "@production-ready/ui";

import { LanguageField, languagePackFieldId } from "./LanguagePackFieldsPanel";
import type { LanguagePackField, LanguagePackSections } from "@/lib/api";
import { EMPTY_EVIDENCE_RESPONSE_KEYS, emptyEvidenceResponsePreview, isEmptyEvidenceResponseField } from "@/lib/empty-evidence-response";
import { t } from "@/lib/i18n";
import type { LanguagePackTexts } from "@/lib/language-pack";

/** 全体とプロファイルで共通の、根拠がないときの基本本文の編集と確認。 */
export function EmptyEvidenceResponseSettings({
  idPrefix, fields, baseline, texts, errors, disabled, profileOverrides, onChange,
}: {
  idPrefix: string;
  fields: LanguagePackField[];
  baseline: LanguagePackSections;
  texts: LanguagePackTexts;
  errors: Record<string, string>;
  disabled: boolean;
  profileOverrides?: LanguagePackSections;
  onChange: (key: string, value: string) => void;
}) {
  const profile = profileOverrides !== undefined;
  const id = `${idPrefix}-empty-evidence-response`;
  const preview = emptyEvidenceResponsePreview(texts);
  return (
    <section aria-labelledby={id} className="space-y-4" data-testid="empty-evidence-response-settings">
      <div className="space-y-2">
        <h3 id={id} className="text-base font-semibold text-fg">{t("settings.language.emptyEvidence.title")}</h3>
        <p className="text-sm text-fg-muted">{t("settings.language.emptyEvidence.helper")}</p>
        <p className="text-xs text-fg-muted">{t(profile ? "settings.language.emptyEvidence.profileSave" : "settings.language.emptyEvidence.globalSave")}</p>
      </div>
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
            overridden={profile ? Object.hasOwn(profileOverrides[field.section] ?? {}, field.key) : undefined}
            changedLabel={t(profile ? "settings.language.emptyEvidence.overridden" : "settings.language.changed")}
            unchangedLabel={t(profile ? "settings.language.emptyEvidence.inherited" : "settings.language.emptyEvidence.builtin")}
            resetLabel={t(profile ? "searchAnswerProfiles.languagePack.reset" : "settings.language.resetField")}
            onChange={(value) => onChange(key, value)}
          />;
        })}
      </FormGrid>
      <div className="space-y-2" role="region" aria-labelledby={`${id}-preview-title`}>
        <h4 id={`${id}-preview-title`} className="text-sm font-semibold text-fg">{t("settings.language.emptyEvidence.preview")}</h4>
        <p className="text-xs text-fg-muted">{t("settings.language.emptyEvidence.previewHelp")}</p>
        <p lang={resolveContentLang(undefined, preview)} className="whitespace-pre-wrap break-words text-sm text-fg" data-testid="empty-evidence-response-preview">{preview}</p>
      </div>
    </section>
  );
}
