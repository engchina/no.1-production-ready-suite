"use client";

import {
  ButtonLink,
  DataTable,
  FormActionBar,
  FormStatus,
  PageBody,
  Section,
  SelectField,
  TableSkeleton,
  TimedLoadingState,
  INFORMATION_TABLE_VISIBLE_ROWS,
} from "@production-ready/ui";
import { useState } from "react";
import { Link } from "react-router-dom";
import { RotateCcw, Save, Settings2, Undo2 } from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import {
  ApiError,
  type ChunkingStrategyName,
  type FileTypeDefaultEntry,
  type FileTypeDefaultRow,
  type FileTypeDefaultsSettingsData,
  type FileTypeName,
  type ParserAdapterBackend,
  type PreprocessProfileName,
} from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { t, type I18nKey } from "@/lib/i18n";
import { useFileTypeDefaultsSettings, useUpdateFileTypeDefaultsSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { parserBackendLabel } from "@/lib/source-profile-labels";
import { toast } from "@/lib/toast";

import {
  draftFromBuiltin,
  draftFromSettings,
  draftsEqual,
  entryWithPreprocess,
  followsBuiltin,
  parserOptionsFor,
  updatePayload,
  withCurrentValue,
  type FileTypeDraft,
} from "./FileTypeDefaultsSettingsClient.logic";

function preprocessLabel(profile: PreprocessProfileName) {
  return t(`settings.preprocess.profile.${profile}` as I18nKey);
}

function chunkingLabel(strategy: ChunkingStrategyName) {
  return t(`settings.chunking.strategy.${strategy}` as I18nKey);
}

/** 文書の種類ごとのファイル準備・解析エンジン・分割方式の既定（全体の設定の層。#4732）。 */
export function FileTypeDefaultsSettingsClient() {
  const query = useFileTypeDefaultsSettings();
  const save = useUpdateFileTypeDefaultsSettings();
  const [draft, setDraft] = useState<FileTypeDraft | null>(null);
  const [base, setBase] = useState<FileTypeDefaultsSettingsData | null>(null);
  if (query.data && query.data !== base) {
    setBase(query.data);
    // server の値が変わったレンダーだけ、未編集なら下書きをそろえる（保存の失敗で選択を戻さない）。
    if (draft === null || (base !== null && draftsEqual(draft, draftFromSettings(base)))) {
      setDraft(draftFromSettings(query.data));
    }
  }

  const settings = query.data;
  const dirty = Boolean(settings && draft && !draftsEqual(draft, draftFromSettings(settings)));
  useLeaveGuard(dirty, save.isPending);

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.fileTypeDefaults.loading")}
          operationKey="settings-file-type-defaults-load"
          placement="page"
          testId="settings-file-type-defaults-loading"
        >
          <TableSkeleton columns={4} />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ApiErrorState
          error={query.error}
          fallback={t("settings.fileTypeDefaults.loadError")}
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  if (!settings || !draft) return null;

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.fileTypeDefaults.saveError");
  const chunkingOptions = settings.chunking_options;
  const globals = settings.global_defaults;

  function updateRow(fileType: FileTypeName, next: FileTypeDefaultEntry) {
    save.reset();
    setDraft((current) => (current ? { ...current, [fileType]: next } : current));
  }

  function submit() {
    if (!draft || save.isPending) return;
    save.mutate(updatePayload(draft, settings?.rows ?? []), {
      onSuccess: (data) => {
        setBase(data);
        setDraft(draftFromSettings(data));
        toast.success(t("settings.fileTypeDefaults.actions.saved"));
      },
    });
  }

  return (
    <PageBody wide>
      <Section>
        <p className="text-sm text-fg-muted">
          {t("settings.fileTypeDefaults.summary", {
            preprocess: preprocessLabel(globals.preprocess_profile),
            parser: parserBackendLabel(globals.parser_adapter_backend),
            chunking: chunkingLabel(globals.chunking_strategy),
          })}
        </p>
        {/* Section は grid なので、min-w-0 が無いと表が 375px で親を押し広げる（表の中で横スクロールする）。 */}
        <div className="min-w-0 space-y-4">
          <DataTable<FileTypeDefaultRow>
            ariaLabel={t("settings.fileTypeDefaults.tableLabel")}
            rows={settings.rows}
            getRowKey={(row) => row.file_type}
            stickyHeader
            visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
            rowProps={(row) => ({ "data-testid": `file-type-row-${row.file_type}` })}
            renderRowDetail={(row) =>
              row.builtin_unavailable_reason && followsBuiltin(row, draft[row.file_type]) ? (
                <p className="text-xs text-fg-muted">{row.builtin_unavailable_reason}</p>
              ) : null
            }
            columns={[
              {
                key: "file_type",
                header: t("settings.fileTypeDefaults.column.fileType"),
                rowHeader: true,
                className: "align-top min-w-32",
                render: (row) => (
                  <div>
                    <div className="text-sm font-medium text-fg">
                      {t(`settings.fileTypeDefaults.type.${row.file_type}` as I18nKey)}
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-1.5 font-mono text-xs text-fg-muted">
                      {row.extensions.map((extension) => (
                        <span key={extension}>{extension}</span>
                      ))}
                    </div>
                  </div>
                ),
              },
              {
                key: "preprocess",
                header: t("settings.fileTypeDefaults.column.preprocess"),
                className: "align-top min-w-44",
                render: (row) => {
                  const entry = draft[row.file_type];
                  const options = withCurrentValue(row.preprocess_options, entry.preprocess_profile);
                  return (
                    <div className="space-y-1">
                      <SelectField<string>
                        id={`file-type-${row.file_type}-preprocess`}
                        label={`${t(`settings.fileTypeDefaults.type.${row.file_type}` as I18nKey)} ${t("settings.fileTypeDefaults.column.preprocess")}`}
                        labelHidden
                        size="sm"
                        value={entry.preprocess_profile ?? ""}
                        emptyOptionLabel={t("settings.fileTypeDefaults.inherit")}
                        options={options.map((value) => ({ value, label: preprocessLabel(value) }))}
                        disabled={save.isPending}
                        data-testid={`file-type-${row.file_type}-preprocess`}
                        onValueChange={(value) =>
                          updateRow(
                            row.file_type,
                            entryWithPreprocess(row, entry, (value || null) as PreprocessProfileName | null)
                          )
                        }
                      />
                      {row.unavailable_preprocess_profiles.length ? (
                        <ButtonLink
                          to={APP_ROUTES.settingsServices}
                          linkComponent={Link}
                          variant="ghost"
                          size="sm"
                          icon={Settings2}
                        >
                          {t("settings.fileTypeDefaults.goPreprocess")}
                        </ButtonLink>
                      ) : null}
                    </div>
                  );
                },
              },
              {
                key: "parser",
                header: t("settings.fileTypeDefaults.column.parser"),
                className: "align-top min-w-44",
                render: (row) => {
                  const entry = draft[row.file_type];
                  const options = withCurrentValue<ParserAdapterBackend>(
                    parserOptionsFor(row, entry.preprocess_profile),
                    entry.parser_adapter_backend
                  );
                  return (
                    <div className="space-y-1">
                      <SelectField<string>
                        id={`file-type-${row.file_type}-parser`}
                        label={`${t(`settings.fileTypeDefaults.type.${row.file_type}` as I18nKey)} ${t("settings.fileTypeDefaults.column.parser")}`}
                        labelHidden
                        size="sm"
                        value={entry.parser_adapter_backend ?? ""}
                        emptyOptionLabel={t("settings.fileTypeDefaults.inherit")}
                        options={options.map((value) => ({ value, label: parserBackendLabel(value) }))}
                        disabled={save.isPending}
                        data-testid={`file-type-${row.file_type}-parser`}
                        onValueChange={(value) =>
                          updateRow(row.file_type, {
                            ...entry,
                            parser_adapter_backend: (value || null) as ParserAdapterBackend | null,
                          })
                        }
                      />
                    </div>
                  );
                },
              },
              {
                key: "chunking",
                header: t("settings.fileTypeDefaults.column.chunking"),
                className: "align-top min-w-44",
                render: (row) => {
                  const entry = draft[row.file_type];
                  return (
                    <SelectField<string>
                      id={`file-type-${row.file_type}-chunking`}
                      label={`${t(`settings.fileTypeDefaults.type.${row.file_type}` as I18nKey)} ${t("settings.fileTypeDefaults.column.chunking")}`}
                      labelHidden
                      size="sm"
                      value={entry.chunking_strategy ?? ""}
                      emptyOptionLabel={t("settings.fileTypeDefaults.inherit")}
                      options={chunkingOptions.map((value) => ({ value, label: chunkingLabel(value) }))}
                      disabled={save.isPending}
                      data-testid={`file-type-${row.file_type}-chunking`}
                      onValueChange={(value) =>
                        updateRow(row.file_type, {
                          ...entry,
                          chunking_strategy: (value || null) as ChunkingStrategyName | null,
                        })
                      }
                    />
                  );
                },
              },
            ]}
          />
          <p className="text-xs text-fg-muted">{t("settings.fileTypeDefaults.reprocessNote")}</p>
          <FormActionBar
            ariaLabel={t("settings.fileTypeDefaults.actions.label")}
            primaryActions={[
              {
                id: "save",
                label: t("settings.fileTypeDefaults.actions.save"),
                icon: Save,
                loading: save.isPending,
                disabled: !dirty,
                onClick: submit,
              },
            ]}
            secondaryActions={[
              {
                id: "restore-defaults",
                label: t("settings.fileTypeDefaults.actions.restoreDefaults"),
                icon: Undo2,
                disabled: save.isPending || draftsEqual(draft, draftFromBuiltin(settings)),
                onClick: () => {
                  save.reset();
                  setDraft(draftFromBuiltin(settings));
                },
              },
              {
                id: "reset",
                label: t("settings.fileTypeDefaults.actions.reset"),
                icon: RotateCcw,
                disabled: !dirty || save.isPending,
                onClick: () => {
                  save.reset();
                  setDraft(draftFromSettings(settings));
                },
              },
            ]}
            status={
              save.isError ? (
                <FormStatus tone="danger" message={saveError} />
              ) : dirty ? (
                <FormStatus tone="warning" message={t("settings.fileTypeDefaults.actions.unsaved")} />
              ) : null
            }
          />
        </div>
      </Section>
    </PageBody>
  );
}

export default FileTypeDefaultsSettingsClient;
