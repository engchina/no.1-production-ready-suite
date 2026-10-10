"use client";

import { FormStatus, SelectField, type SelectFieldOption, Switch } from "@production-ready/ui";

import { t } from "@/lib/i18n";
import { useEntityIndexCoverage } from "@/lib/queries";

/** 1 回の検索で加える根拠の上限の範囲（backend の `entity_expansion_max_chunks` の検証と同じ）。 */
export const ENTITY_EXPANSION_MAX_CHUNKS_MIN = 1;
export const ENTITY_EXPANSION_MAX_CHUNKS_MAX = 20;
/** 上限を選ばないときの値（backend の Settings の既定）。 */
export const ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT = 6;

const MAX_CHUNKS_OPTIONS: SelectFieldOption<string>[] = Array.from(
  { length: ENTITY_EXPANSION_MAX_CHUNKS_MAX - ENTITY_EXPANSION_MAX_CHUNKS_MIN + 1 },
  (_, index) => {
    const count = index + ENTITY_EXPANSION_MAX_CHUNKS_MIN;
    return {
      value: String(count),
      label:
        count === ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT
          ? t("searchAnswerProfiles.entityExpansion.maxChunksDefault", { count })
          : t("searchAnswerProfiles.entityExpansion.maxChunksValue", { count }),
    };
  },
);

export interface EntityExpansionValue {
  entity_expansion_enabled?: boolean | null;
  entity_expansion_max_chunks?: number | null;
}

/**
 * 開閉を切り替えたときの query の差分。既定（使う。#1402）と同じ on は null で保存して既定に
 * 追従させ、off だけを false で保存する。どちらも上限を一緒に外す（#1388）。
 */
export function entityExpansionPatch(enabled: boolean): Required<EntityExpansionValue> {
  return enabled
    ? { entity_expansion_enabled: null, entity_expansion_max_chunks: null }
    : { entity_expansion_enabled: false, entity_expansion_max_chunks: null };
}

/** 保存値から開閉を読む。null / 未指定は既定（使う。#1402）で、false だけが使わない。 */
export function isEntityExpansionEnabled(value: EntityExpansionValue): boolean {
  return value.entity_expansion_enabled !== false;
}

/**
 * 検索・回答プロファイルの「実体でつながる根拠を 1 段広げる」（#1362 / #1388）。
 *
 * 環境変数は持たず、このプロファイルの検索だけに効く（既定 on。#1402）。実体は文書レシピで
 * 実体の索引を有効にした文書にだけあるため、参照先のナレッジベースにその文書が 1 つも無いときは、
 * 開閉の下に案内を出す（効かない設定を黙って受け付けない）。
 */
export function EntityExpansionRow({
  value,
  knowledgeBaseIds,
  disabled,
  onChange,
}: {
  value: EntityExpansionValue;
  knowledgeBaseIds: readonly string[];
  disabled: boolean;
  onChange: (patch: EntityExpansionValue) => void;
}) {
  const enabled = isEntityExpansionEnabled(value);
  const coverage = useEntityIndexCoverage(knowledgeBaseIds, knowledgeBaseIds.length > 0);
  const noEntityDocuments = coverage.data?.document_count === 0;
  const labelId = "search-answer-profile-entity-expansion-label";
  const descriptionId = "search-answer-profile-entity-expansion-helper";
  const coverageId = "search-answer-profile-entity-expansion-coverage";
  return (
    <div
      className="grid gap-3 rounded-lg border border-border bg-surface-sunken p-3 md:grid-cols-[minmax(10rem,14rem)_minmax(0,1fr)]"
      data-testid="search-answer-profile-entity-expansion"
    >
      <h3 className="text-sm font-medium text-fg">{t("searchAnswerProfiles.entityExpansion.title")}</h3>
      <div className="min-w-0 space-y-3">
        <div className="flex items-start gap-3">
          <Switch
            id="search-answer-profile-entity-expansion"
            checked={enabled}
            disabled={disabled}
            aria-labelledby={labelId}
            aria-describedby={noEntityDocuments ? `${descriptionId} ${coverageId}` : descriptionId}
            onCheckedChange={(checked) => onChange(entityExpansionPatch(checked))}
            className="mt-0.5"
          />
          <div className="min-w-0">
            <span id={labelId} className="text-sm text-fg">
              {t("searchAnswerProfiles.entityExpansion.enabled")}
            </span>
            <p id={descriptionId} className="mt-0.5 text-xs leading-relaxed text-fg-muted">
              {t("searchAnswerProfiles.entityExpansion.helper")}
            </p>
          </div>
        </div>
        {noEntityDocuments ? (
          <div id={coverageId} data-testid="search-answer-profile-entity-expansion-coverage">
            <FormStatus
              tone={enabled ? "warning" : "info"}
              message={t("searchAnswerProfiles.entityExpansion.noEntityDocuments")}
            />
          </div>
        ) : null}
        {enabled ? (
          <SelectField
            id="search-answer-profile-entity-expansion-max-chunks"
            label={t("searchAnswerProfiles.entityExpansion.maxChunks")}
            helper={t("searchAnswerProfiles.entityExpansion.maxChunksHelper")}
            width="md"
            value={String(value.entity_expansion_max_chunks ?? ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT)}
            options={MAX_CHUNKS_OPTIONS}
            disabled={disabled}
            onValueChange={(next) => {
              const count = Number(next);
              onChange({
                // 既定と同じ値は保存しない（既定を変えたときに追従させる）。
                entity_expansion_max_chunks:
                  count === ENTITY_EXPANSION_MAX_CHUNKS_DEFAULT ? null : count,
              });
            }}
          />
        ) : null}
      </div>
    </div>
  );
}
