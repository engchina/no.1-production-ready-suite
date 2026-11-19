import {
  Button,
  FormActionBar,
  FormSkeleton,
  FormStatus,
  SelectField,
  type SelectFieldOption,
  TextField,
  TextareaField,
  TimedLoadingState,
} from "@production-ready/ui";
import type { UseMutationResult } from "@tanstack/react-query";
import { History, Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { ApiError, type ExtractionFieldDefinition, type ExtractionFieldValueType } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import {
  useExtractionFieldsSettings,
  useResetExtractionFieldsSettings,
  useUpdateExtractionFieldsSettings,
} from "@/lib/queries";

import {
  EXTRACTION_FIELD_DESCRIPTION_MAX,
  EXTRACTION_FIELD_NAME_MAX,
  EXTRACTION_FIELD_VALUE_TYPES,
  MAX_EXTRACTION_FIELDS,
  type ExtractionFieldRow,
  definitionsFromRows,
  newExtractionFieldRow,
  rowsFromDefinitions,
  sameDefinitions,
  validateExtractionFieldRows,
} from "./ExtractionFieldsEditor.logic";

const VALUE_TYPE_OPTIONS: SelectFieldOption<ExtractionFieldValueType>[] =
  EXTRACTION_FIELD_VALUE_TYPES.map((value) => ({
    value,
    label: t(`settings.extractionFields.valueType.${value}`),
  }));

/**
 * 項目抽出で取り出す項目の定義（`/api/settings/extraction-fields`）を編集する（#528）。
 * 全体の既定の定義（KB に定義が無い文書に使う。#548）。文書解析の「解析後の処理」の項目抽出の中に置く。
 * 一度も保存していない環境は標準の項目を使う（#556）。「標準の項目に戻す」は標準の項目を編集欄に入れるだけで、
 * 「保存」で保存した定義を消す（#5543）。
 */
export function ExtractionFieldsEditor() {
  const query = useExtractionFieldsSettings();
  const save = useUpdateExtractionFieldsSettings();
  const reset = useResetExtractionFieldsSettings();

  if (query.isPending) {
    // ほかの読み込み中と同じく、先頭に文言と経過時間の行、その下に内容の形の Skeleton（#2510）。
    return (
      <TimedLoadingState
        label={t("settings.extractionFields.loading")}
        operationKey="extraction-fields-load"
        testId="extraction-fields-loading"
      >
        <FormSkeleton fields={2} />
      </TimedLoadingState>
    );
  }
  if (query.isError || !query.data) {
    return (
      <ApiErrorState
        error={query.error}
        fallback={t("settings.extractionFields.loadError")}
        onRetry={() => void query.refetch()}
      />
    );
  }
  const usesStandard = query.data.uses_standard;
  return (
    <ExtractionFieldsForm
      saved={query.data.fields}
      save={save}
      description={t("settings.extractionFields.description")}
      notice={
        usesStandard ? (
          <FormStatus
            tone="info"
            className="text-xs"
            message={t("settings.extractionFields.usingStandard")}
          />
        ) : null
      }
      restoreDefaults={{
        label: t("settings.extractionFields.resetStandard"),
        fields: query.data.standard_fields,
        // 標準の項目を使っている間は戻すものが無い（編集中の変更は「変更を破棄」で戻す）。
        available: !usesStandard,
        pendingMessage: t("settings.extractionFields.resetStandard.pending"),
        save: reset,
      }}
    />
  );
}

/** 保存の mutation。保存した定義（`fields`）を返す。全体の既定と KB の定義（#548）で共通。 */
export type ExtractionFieldsSaveMutation = UseMutationResult<
  { fields: ExtractionFieldDefinition[] },
  Error,
  ExtractionFieldDefinition[]
>;

/**
 * 既定に戻す（全体の既定の「標準の項目に戻す」・KB の「全体の既定に戻す」。#5543）。押すと `fields` を
 * 編集欄に入れるだけで、そのまま「保存」すると `save`（保存した定義を消す）を送る。写しを保存しないので、
 * 戻す先が後で変わっても追従する。戻した後に項目を編集したら、ふつうの保存（自分の定義）になる。
 */
export interface ExtractionFieldsRestoreDefaults {
  label: string;
  fields: ExtractionFieldDefinition[];
  /** 戻すものがあるか（全体の既定は標準の項目を使っている間は無い）。 */
  available: boolean;
  /** 戻した後、保存するまでの案内。 */
  pendingMessage: string;
  save: UseMutationResult<{ fields: ExtractionFieldDefinition[] }, Error, void>;
  onSaved?: () => void;
}

/** 項目の定義の編集欄。保存先は `save` が決める（全体の既定か KB の定義）。 */
export function ExtractionFieldsForm({
  saved,
  save,
  description,
  notice,
  restoreDefaults,
  testId = "extraction-fields-editor",
}: {
  saved: ExtractionFieldDefinition[];
  save: ExtractionFieldsSaveMutation;
  description: string;
  /** 説明の下に出す定義の状態（全体の既定の「標準の項目を使っています」など）。 */
  notice?: ReactNode;
  restoreDefaults?: ExtractionFieldsRestoreDefaults;
  testId?: string;
}) {
  const [rows, setRows] = useState<ExtractionFieldRow[]>(() => rowsFromDefinitions(saved));
  // 編集欄が基にした保存値。保存値が変わったレンダーで、未編集なら編集欄を保存値へそろえる
  // （編集中の内容は背景の再取得で上書きしない。UX 契約 workspace-state）。
  const [base, setBase] = useState(saved);
  const [attempted, setAttempted] = useState(false);
  // 既定に戻した後、編集せずにいるか（保存で戻す先の削除を送る）。
  const [restorePending, setRestorePending] = useState(false);
  const restoreSave = restoreDefaults?.save;
  const saving = save.isPending || Boolean(restoreSave?.isPending);
  const saveError = save.error ?? restoreSave?.error ?? null;
  if (saved !== base) {
    setBase(saved);
    if (sameDefinitions(base, rows)) setRows(rowsFromDefinitions(saved));
  }
  const dirty = restorePending || !sameDefinitions(saved, rows);
  useLeaveGuard(dirty);
  const errors = validateExtractionFieldRows(rows);
  const visibleErrors = attempted ? errors : {};
  const atLimit = rows.length >= MAX_EXTRACTION_FIELDS;

  function clearStatus() {
    save.reset();
    restoreSave?.reset();
    setRestorePending(false);
  }

  function edit(key: string, update: Partial<ExtractionFieldDefinition>) {
    clearStatus();
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...update } : row)));
  }

  function add() {
    clearStatus();
    const row = newExtractionFieldRow();
    setRows((current) => [...current, row]);
    // 追加した行の項目名へフォーカスを移す（描画の後）。
    requestAnimationFrame(() => document.getElementById(`${row.key}-name`)?.focus());
  }

  function remove(key: string) {
    clearStatus();
    setRows((current) => current.filter((row) => row.key !== key));
  }

  function discard() {
    clearStatus();
    setAttempted(false);
    setRows(rowsFromDefinitions(saved));
  }

  function restore() {
    if (!restoreDefaults) return;
    clearStatus();
    setAttempted(false);
    setRows(rowsFromDefinitions(restoreDefaults.fields));
    setRestorePending(true);
  }

  function applySaved(fields: ExtractionFieldDefinition[]) {
    setAttempted(false);
    setRestorePending(false);
    setBase(fields);
    setRows(rowsFromDefinitions(fields));
  }

  function submit() {
    if (restorePending && restoreSave) {
      restoreSave.mutate(undefined, {
        onSuccess: (data) => {
          applySaved(data.fields);
          restoreDefaults?.onSaved?.();
        },
      });
      return;
    }
    setAttempted(true);
    const firstError = rows.find((row) => errors[row.key]);
    if (firstError) {
      document.getElementById(`${firstError.key}-name`)?.focus();
      return;
    }
    save.mutate(definitionsFromRows(rows), { onSuccess: (data) => applySaved(data.fields) });
  }

  return (
    <div className="space-y-3" data-testid={testId}>
      <p className="text-xs leading-relaxed text-fg-muted">{description}</p>
      {notice}
      {rows.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-4 text-sm text-fg-muted">
          {t("settings.extractionFields.none")}
        </p>
      ) : (
        // 表形式の定義の行は全幅。名前は md・型は sm を保ち、説明に残りの幅を使う。
        // 1 行に 1 項目の欄を並べる表の形の編集の行なので、説明の欄（残りの幅）は欄の幅の段の検査
        // （findFieldsOffWidthScale）の対象から外す（表の中の欄と同じ扱い）。
          <ol className="space-y-2" data-field-width-exempt="">
            {rows.map((row, index) => {
              const error = visibleErrors[row.key];
              const rowLabel = t("settings.extractionFields.rowLabel", { index: index + 1 });
              return (
                <li
                  key={row.key}
                  aria-label={rowLabel}
                  className="grid min-w-0 grid-cols-1 gap-3 rounded-md border border-border bg-surface p-3 xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)_12rem_auto] xl:items-start"
                >
                  <TextField
                    id={`${row.key}-name`}
                    width="md"
                    label={t("settings.extractionFields.name")}
                    required
                    value={row.name}
                    maxLength={EXTRACTION_FIELD_NAME_MAX}
                    autoComplete="off"
                    disabled={saving}
                    error={error ? t(`settings.extractionFields.error.${error}`) : undefined}
                    onValueChange={(name) => edit(row.key, { name })}
                  />
                  <TextareaField
                    id={`${row.key}-description`}
                    rows={3}
                    width="full"
                    label={t("settings.extractionFields.fieldDescription")}
                    value={row.description}
                    maxLength={EXTRACTION_FIELD_DESCRIPTION_MAX}
                    autoComplete="off"
                    disabled={saving}
                    onValueChange={(description) => edit(row.key, { description })}
                  />
                  <SelectField
                    id={`${row.key}-value-type`}
                    width="sm"
                    label={t("settings.extractionFields.valueType")}
                    value={row.value_type}
                    options={VALUE_TYPE_OPTIONS}
                    onValueChange={(value_type) => edit(row.key, { value_type })}
                  />
                  {/* 入力欄のラベルの高さの分だけ下げ、入力欄と同じ行にそろえる（xl 以上）。 */}
                  <div className="flex justify-end xl:pt-6">
                    <Button
                      type="button"
                      variant="ghost"
                      tone="danger"
                      iconOnly
                      icon={Trash2}
                      aria-label={t("settings.extractionFields.remove", { index: index + 1 })}
                      disabled={saving}
                      onClick={() => remove(row.key)}
                    />
                  </div>
                </li>
              );
            })}
          </ol>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          icon={Plus}
          disabled={atLimit || saving}
          onClick={add}
        >
          {t("settings.extractionFields.add")}
        </Button>
        {atLimit ? (
          <span className="text-xs text-fg-muted">
            {t("settings.extractionFields.limit", { max: MAX_EXTRACTION_FIELDS })}
          </span>
        ) : null}
      </div>
      <FormActionBar
        ariaLabel={t("settings.extractionFields.actionsLabel")}
        primaryActions={[
          {
            id: "save",
            label: t("settings.extractionFields.save"),
            icon: Save,
            loading: saving,
            disabled: !dirty,
            onClick: submit,
          },
        ]}
        secondaryActions={[
          {
            id: "discard",
            label: t("settings.extractionFields.discard"),
            icon: RotateCcw,
            disabled: !dirty || saving,
            onClick: discard,
          },
        ]}
        restoreDefaultsAction={
          restoreDefaults
            ? {
                id: "restore-defaults",
                label: restoreDefaults.label,
                icon: History,
                disabled: saving || restorePending || !restoreDefaults.available,
                onClick: restore,
              }
            : undefined
        }
        status={
          saveError ? (
            <FormStatus
              tone="danger"
              message={
                saveError instanceof ApiError
                  ? saveError.message
                  : t("settings.extractionFields.saveError")
              }
            />
          ) : restorePending && restoreDefaults ? (
            <FormStatus tone="warning" message={restoreDefaults.pendingMessage} />
          ) : (save.isSuccess || restoreSave?.isSuccess) && !dirty ? (
            <FormStatus tone="success" message={t("settings.extractionFields.saved")} />
          ) : null
        }
      />
    </div>
  );
}
