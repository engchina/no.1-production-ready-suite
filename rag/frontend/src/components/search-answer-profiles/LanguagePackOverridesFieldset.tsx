"use client";

import { FormSkeleton, StatusBadge, TimedLoadingState } from "@production-ready/ui";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { LanguagePackFieldsPanel, useLanguagePackPanelState } from "@/components/settings/LanguagePackFieldsPanel";
import type { LanguagePackSections } from "@/lib/api";
import { t } from "@/lib/i18n";
import {
  type LanguagePackTexts,
  languagePackErrors,
  overridesFromTexts,
  textsFromSections,
} from "@/lib/language-pack";
import { useLanguagePack } from "@/lib/queries";

/** 全体の値に上書きを重ねた「節 → 項目 → 値」。 */
function overlay(base: LanguagePackSections, overrides: LanguagePackSections): LanguagePackSections {
  const merged: LanguagePackSections = {};
  for (const [section, values] of Object.entries(base)) merged[section] = { ...values, ...overrides[section] };
  return merged;
}

/**
 * 検索・回答プロファイルの言語資源の上書き（#2181）。全体の言語資源の値に重ね、このプロファイルの検索・回答の
 * 間だけ使う。取込の項目は文書の分割と食い違うので出さない（検索・回答の項目だけ）。
 */
export function LanguagePackOverridesFieldset({
  value,
  disabled,
  onChange,
}: {
  value: LanguagePackSections;
  disabled: boolean;
  onChange: (overrides: LanguagePackSections) => void;
}) {
  const query = useLanguagePack();
  const panel = useLanguagePackPanelState();
  const [texts, setTexts] = useState<LanguagePackTexts | null>(null);
  const [synced, setSynced] = useState<string | null>(null);
  const data = query.data;
  const fields = data ? data.fields.filter((field) => !field.ingest) : [];
  // 持ち主の値（保存・変更の破棄）が変わったときだけ、欄の文字を合わせる（入力中の文字は上書きしない）。
  const valueKey = JSON.stringify(value);
  if (data && valueKey !== synced) {
    setSynced(valueKey);
    const current = texts ? JSON.stringify(overridesFromTexts(fields, texts, data.effective)) : null;
    if (current !== valueKey) setTexts(textsFromSections(fields, overlay(data.effective, value)));
  }
  const count = Object.values(value).reduce((sum, values) => sum + Object.keys(values).length, 0);

  return (
    <fieldset
      className="space-y-3 rounded-lg border border-border p-4"
      data-testid="search-answer-profile-language-pack"
    >
      <legend className="px-1 text-sm font-semibold text-fg">{t("searchAnswerProfiles.languagePack.title")}</legend>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs text-fg-muted">{t("searchAnswerProfiles.languagePack.helper")}</p>
        {count > 0 ? (
          <StatusBadge
            variant="info"
            icon={false}
            label={t("searchAnswerProfiles.languagePack.count", { count })}
          />
        ) : null}
      </div>
      {query.isPending ? (
        <TimedLoadingState
          label={t("settings.language.loading")}
          operationKey="search-answer-profile-language-pack"
          testId="search-answer-profile-language-pack-loading"
        >
          <FormSkeleton fields={2} />
        </TimedLoadingState>
      ) : query.isError || !data || !texts ? (
        <ApiErrorState
          error={query.error}
          fallback={t("settings.language.loadError")}
          onRetry={() => void query.refetch()}
        />
      ) : (
        <LanguagePackFieldsPanel
          idPrefix="profile-language"
          fields={fields}
          baseline={data.effective}
          texts={texts}
          errors={languagePackErrors(fields, texts)}
          disabled={disabled}
          state={panel}
          changedLabel={t("searchAnswerProfiles.languagePack.changed")}
          resetLabel={t("searchAnswerProfiles.languagePack.reset")}
          onChange={(key, text) => {
            const next = { ...texts, [key]: text };
            setTexts(next);
            const overrides = overridesFromTexts(fields, next, data.effective);
            setSynced(JSON.stringify(overrides));
            onChange(overrides);
          }}
        />
      )}
    </fieldset>
  );
}
