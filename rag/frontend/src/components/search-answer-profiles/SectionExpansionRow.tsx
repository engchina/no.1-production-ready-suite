"use client";

import { FormGrid, SelectField, type SelectFieldOption, Switch } from "@production-ready/ui";

import { t } from "@/lib/i18n";

/** 広げる段の上限の範囲と既定（backend の `section_expansion_max_levels` の検証と Settings の既定と同じ）。 */
export const SECTION_EXPANSION_MAX_LEVELS_MIN = 1;
export const SECTION_EXPANSION_MAX_LEVELS_MAX = 8;
export const SECTION_EXPANSION_MAX_LEVELS_DEFAULT = 4;
/** 根拠の入力の上限（モデルの入力の上限に対する %）の選択肢と既定（backend は 10〜90）。 */
export const SECTION_EXPANSION_INPUT_TOKEN_PERCENTS = [10, 20, 30, 40, 50, 60, 70, 80, 90] as const;
export const SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT = 50;
export interface SectionExpansionValue {
  section_expansion_enabled?: boolean | null;
  section_expansion_max_levels?: number | null;
  section_expansion_input_token_percent?: number | null;
}

function options(
  values: readonly number[],
  defaultValue: number,
  label: (value: number, isDefault: boolean) => string,
): SelectFieldOption<string>[] {
  return values.map((value) => ({ value: String(value), label: label(value, value === defaultValue) }));
}

const MAX_LEVEL_OPTIONS = options(
  Array.from(
    { length: SECTION_EXPANSION_MAX_LEVELS_MAX - SECTION_EXPANSION_MAX_LEVELS_MIN + 1 },
    (_, index) => index + SECTION_EXPANSION_MAX_LEVELS_MIN,
  ),
  SECTION_EXPANSION_MAX_LEVELS_DEFAULT,
  (count, isDefault) =>
    t(
      isDefault
        ? "searchAnswerProfiles.sectionExpansion.maxLevelsDefault"
        : "searchAnswerProfiles.sectionExpansion.maxLevelsValue",
      { count },
    ),
);
const INPUT_TOKEN_OPTIONS = options(
  SECTION_EXPANSION_INPUT_TOKEN_PERCENTS,
  SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT,
  (percent, isDefault) =>
    t(
      isDefault
        ? "searchAnswerProfiles.sectionExpansion.inputTokenPercentDefault"
        : "searchAnswerProfiles.sectionExpansion.inputTokenPercentValue",
      { percent },
    ),
);
/**
 * 開閉を切り替えたときの query の差分。既定（使う）と同じ on は null で保存して既定に追従させ、off だけを
 * false で保存する。どちらも上限を一緒に外す（#1436）。
 */
export function sectionExpansionPatch(enabled: boolean): Required<SectionExpansionValue> {
  return {
    section_expansion_enabled: enabled ? null : false,
    section_expansion_max_levels: null,
    section_expansion_input_token_percent: null,
  };
}

/** 保存値から開閉を読む。null / 未指定は既定（使う）で、false だけが使わない。 */
export function isSectionExpansionEnabled(value: SectionExpansionValue): boolean {
  return value.section_expansion_enabled !== false;
}

/** 選んだ値の保存値。既定と同じ値は保存しない（null。既定を変えたときに追従させる）。 */
export function sectionExpansionLimitValue(next: string, defaultValue: number): number | null {
  const value = Number(next);
  return value === defaultValue ? null : value;
}

/**
 * 検索・回答プロファイルの「当たった根拠の章節から親の章節へ広げる」（#1436）。
 *
 * 環境変数は持たず、このプロファイルの検索だけに効く（既定 on）。上限（段・根拠の入力
 * の回数。#1437）は on のときだけ出す。4 つの欄は、md で 2 列、xl で 4 列に並べる（ラベルが長く、3 列以上の md
 * の幅では折り返すため）。
 */
export function SectionExpansionRow({
  value,
  disabled,
  onChange,
}: {
  value: SectionExpansionValue;
  disabled: boolean;
  onChange: (patch: SectionExpansionValue) => void;
}) {
  const enabled = isSectionExpansionEnabled(value);
  const labelId = "search-answer-profile-section-expansion-label";
  const descriptionId = "search-answer-profile-section-expansion-helper";
  return (
    <div
      className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]"
      data-testid="search-answer-profile-section-expansion"
    >
      <h3 className="text-sm font-medium text-fg">{t("searchAnswerProfiles.sectionExpansion.title")}</h3>
      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-3">
          <Switch
            id="search-answer-profile-section-expansion"
            checked={enabled}
            disabled={disabled}
            aria-labelledby={labelId}
            aria-describedby={descriptionId}
            onCheckedChange={(checked) => onChange(sectionExpansionPatch(checked))}
            className="mt-0.5"
          />
          <div className="min-w-0">
            <span id={labelId} className="text-sm text-fg">
              {t("searchAnswerProfiles.sectionExpansion.enabled")}
            </span>
            <p id={descriptionId} className="mt-0.5 text-xs leading-relaxed text-fg-muted">
              {t("searchAnswerProfiles.sectionExpansion.helper")}
            </p>
          </div>
        </div>
        {enabled ? (
          <div className="space-y-2">
            {/* 上限の 4 つは「3 段（既定）」などの短い選択（sm。#1891）。 */}
            <FormGrid>
              <SelectField
                id="search-answer-profile-section-expansion-max-levels"
                width="sm"
                label={t("searchAnswerProfiles.sectionExpansion.maxLevels")}
                value={String(value.section_expansion_max_levels ?? SECTION_EXPANSION_MAX_LEVELS_DEFAULT)}
                options={MAX_LEVEL_OPTIONS}
                disabled={disabled}
                onValueChange={(next) =>
                  onChange({
                    section_expansion_max_levels: sectionExpansionLimitValue(
                      next,
                      SECTION_EXPANSION_MAX_LEVELS_DEFAULT,
                    ),
                  })
                }
              />
              <SelectField
                id="search-answer-profile-section-expansion-input-token-percent"
                width="sm"
                label={t("searchAnswerProfiles.sectionExpansion.inputTokenPercent")}
                value={String(
                  value.section_expansion_input_token_percent ?? SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT,
                )}
                options={INPUT_TOKEN_OPTIONS}
                disabled={disabled}
                onValueChange={(next) =>
                  onChange({
                    section_expansion_input_token_percent: sectionExpansionLimitValue(
                      next,
                      SECTION_EXPANSION_INPUT_TOKEN_PERCENT_DEFAULT,
                    ),
                  })
                }
              />
            </FormGrid>
            <p className="text-xs leading-relaxed text-fg-muted">
              {t("searchAnswerProfiles.sectionExpansion.limitsHelper")}
            </p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
