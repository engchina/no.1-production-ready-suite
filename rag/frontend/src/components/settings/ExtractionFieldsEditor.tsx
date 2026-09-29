import {
  Button,
  FormSkeleton,
  FormStatus,
  SelectField,
  type SelectFieldOption,
  TextField,
} from "@engchina/production-ready-ui";
import { Plus, RotateCcw, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { ApiError, type ExtractionFieldDefinition, type ExtractionFieldValueType } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useExtractionFieldsSettings, useUpdateExtractionFieldsSettings } from "@/lib/queries";

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
 * 全体で 1 つの定義。文書解析の「解析後の処理」の項目抽出の中に置く。
 */
export function ExtractionFieldsEditor() {
  const query = useExtractionFieldsSettings();
  if (query.isPending) return <FormSkeleton fields={2} />;
  if (query.isError || !query.data) {
    return (
      <ErrorState
        message={
          query.error instanceof ApiError ? query.error.message : t("settings.extractionFields.loadError")
        }
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <ExtractionFieldsForm saved={query.data.fields} />;
}

function ExtractionFieldsForm({ saved }: { saved: ExtractionFieldDefinition[] }) {
  const save = useUpdateExtractionFieldsSettings();
  const [rows, setRows] = useState<ExtractionFieldRow[]>(() => rowsFromDefinitions(saved));
  // 編集欄が基にした保存値。保存値が変わったレンダーで、未編集なら編集欄を保存値へそろえる
  // （編集中の内容は背景の再取得で上書きしない。UX 契約 workspace-state）。
  const [base, setBase] = useState(saved);
  const [attempted, setAttempted] = useState(false);
  if (saved !== base) {
    setBase(saved);
    if (sameDefinitions(base, rows)) setRows(rowsFromDefinitions(saved));
  }
  const dirty = !sameDefinitions(saved, rows);
  useLeaveGuard(dirty);
  const errors = validateExtractionFieldRows(rows);
  const visibleErrors = attempted ? errors : {};
  const atLimit = rows.length >= MAX_EXTRACTION_FIELDS;

  function edit(key: string, update: Partial<ExtractionFieldDefinition>) {
    save.reset();
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...update } : row)));
  }

  function add() {
    save.reset();
    const row = newExtractionFieldRow();
    setRows((current) => [...current, row]);
    // 追加した行の項目名へフォーカスを移す（描画の後）。
    requestAnimationFrame(() => document.getElementById(`${row.key}-name`)?.focus());
  }

  function remove(key: string) {
    save.reset();
    setRows((current) => current.filter((row) => row.key !== key));
  }

  function discard() {
    save.reset();
    setAttempted(false);
    setRows(rowsFromDefinitions(saved));
  }

  function submit() {
    setAttempted(true);
    const firstError = rows.find((row) => errors[row.key]);
    if (firstError) {
      document.getElementById(`${firstError.key}-name`)?.focus();
      return;
    }
    save.mutate(definitionsFromRows(rows), {
      onSuccess: (data) => {
        setAttempted(false);
        setBase(data.fields);
        setRows(rowsFromDefinitions(data.fields));
      },
    });
  }

  return (
    <div className="space-y-3" data-testid="extraction-fields-editor">
      <p className="text-xs leading-relaxed text-fg-muted">{t("settings.extractionFields.description")}</p>
      {rows.length === 0 ? (
        <p className="rounded-md border border-dashed border-border px-3 py-4 text-sm text-fg-muted">
          {t("settings.extractionFields.none")}
        </p>
      ) : (
        <ol className="space-y-2">
          {rows.map((row, index) => {
            const error = visibleErrors[row.key];
            const rowLabel = t("settings.extractionFields.rowLabel", { index: index + 1 });
            return (
              <li
                key={row.key}
                aria-label={rowLabel}
                className="grid min-w-0 grid-cols-1 gap-3 rounded-md border border-border bg-surface p-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_minmax(0,10rem)_auto] lg:items-start"
              >
                <TextField
                  id={`${row.key}-name`}
                  label={t("settings.extractionFields.name")}
                  value={row.name}
                  maxLength={EXTRACTION_FIELD_NAME_MAX}
                  autoComplete="off"
                  disabled={save.isPending}
                  error={error ? t(`settings.extractionFields.error.${error}`) : undefined}
                  onValueChange={(name) => edit(row.key, { name })}
                />
                <TextField
                  id={`${row.key}-description`}
                  label={t("settings.extractionFields.fieldDescription")}
                  value={row.description}
                  maxLength={EXTRACTION_FIELD_DESCRIPTION_MAX}
                  autoComplete="off"
                  disabled={save.isPending}
                  onValueChange={(description) => edit(row.key, { description })}
                />
                <SelectField
                  id={`${row.key}-value-type`}
                  label={t("settings.extractionFields.valueType")}
                  value={row.value_type}
                  options={VALUE_TYPE_OPTIONS}
                  onValueChange={(value_type) => edit(row.key, { value_type })}
                />
                {/* 入力欄のラベルの高さの分だけ下げ、入力欄と同じ行にそろえる（lg 以上）。 */}
                <div className="flex justify-end lg:pt-6">
                  <Button
                    type="button"
                    variant="ghost"
                    tone="danger"
                    iconOnly
                    icon={Trash2}
                    aria-label={t("settings.extractionFields.remove", { index: index + 1 })}
                    disabled={save.isPending}
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
          disabled={atLimit || save.isPending}
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
      <div className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row sm:flex-wrap sm:items-center">
        <Button
          type="button"
          icon={Save}
          loading={save.isPending}
          disabled={!dirty}
          onClick={submit}
          className="w-full sm:w-auto"
        >
          {t("settings.extractionFields.save")}
        </Button>
        <Button
          type="button"
          variant="secondary"
          icon={RotateCcw}
          disabled={!dirty || save.isPending}
          onClick={discard}
          className="w-full sm:w-auto"
        >
          {t("settings.extractionFields.discard")}
        </Button>
        {save.isSuccess && !dirty ? (
          <FormStatus tone="success" message={t("settings.extractionFields.saved")} />
        ) : null}
        {save.isError ? (
          <FormStatus
            tone="danger"
            message={
              save.error instanceof ApiError
                ? save.error.message
                : t("settings.extractionFields.saveError")
            }
          />
        ) : null}
      </div>
    </div>
  );
}
