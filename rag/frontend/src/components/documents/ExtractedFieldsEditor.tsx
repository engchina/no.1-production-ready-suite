"use client";

import { Check, Plus, Save, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError, type ScalarValue } from "@/lib/api";
import { useUpdateDocumentFields } from "@/lib/queries";
import { t } from "@/lib/i18n";

interface Entry {
  id: number;
  key: string;
  value: string;
}

/** 文字列入力を ScalarValue に推定変換する（数値/真偽/空=null）。 */
function coerce(value: string): ScalarValue {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  if (/^-?\d+$/.test(trimmed)) return Number(trimmed);
  if (/^-?\d+\.\d+$/.test(trimmed)) return Number(trimmed);
  return value;
}

function toEntries(fields: Record<string, unknown>): Entry[] {
  return Object.entries(fields).map(([key, value], i) => ({
    id: i,
    key,
    value: value == null ? "" : String(value),
  }));
}

/** 抽出フィールド（fields スカラ辞書）の編集フォーム。 */
export function ExtractedFieldsEditor({
  documentId,
  fields,
}: {
  documentId: string;
  fields: Record<string, unknown>;
}) {
  const [entries, setEntries] = useState<Entry[]>(() => toEntries(fields));
  const [nextId, setNextId] = useState(entries.length);
  const [dirty, setDirty] = useState(false);
  const update = useUpdateDocumentFields();

  const setEntry = (id: number, patch: Partial<Entry>) => {
    setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, ...patch } : e)));
    setDirty(true);
    update.reset();
  };
  const addEntry = () => {
    setEntries((prev) => [...prev, { id: nextId, key: "", value: "" }]);
    setNextId((n) => n + 1);
    setDirty(true);
  };
  const removeEntry = (id: number) => {
    setEntries((prev) => prev.filter((e) => e.id !== id));
    setDirty(true);
    update.reset();
  };

  const save = () => {
    const payload: Record<string, ScalarValue> = {};
    for (const entry of entries) {
      const key = entry.key.trim();
      if (key) payload[key] = coerce(entry.value);
    }
    update.mutate(
      { id: documentId, fields: payload },
      { onSuccess: () => setDirty(false) }
    );
  };

  return (
    <div className="space-y-3">
      <div className="space-y-2">
        {entries.map((entry) => (
          <div key={entry.id} className="flex items-center gap-2">
            <input
              value={entry.key}
              onChange={(e) => setEntry(entry.id, { key: e.target.value })}
              placeholder="項目名"
              className="w-40 shrink-0 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs font-medium outline-none focus-visible:border-primary"
            />
            <input
              value={entry.value}
              onChange={(e) => setEntry(entry.id, { value: e.target.value })}
              placeholder="値"
              className="min-w-0 flex-1 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm outline-none focus-visible:border-primary"
            />
            <button
              type="button"
              onClick={() => removeEntry(entry.id)}
              aria-label="この項目を削除"
              className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-danger-bg/50 hover:text-danger"
            >
              <Trash2 size={15} aria-hidden />
            </button>
          </div>
        ))}
      </div>

      <button
        type="button"
        onClick={addEntry}
        className="inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-primary hover:underline"
      >
        <Plus size={14} aria-hidden />
        項目を追加
      </button>

      {update.isError ? (
        <p className="rounded-md bg-danger-bg/50 px-3 py-2 text-sm text-danger" role="alert">
          {update.error instanceof ApiError ? update.error.message : "保存に失敗しました。"}
        </p>
      ) : null}

      <div className="flex items-center gap-2 border-t border-border pt-3">
        <Button onClick={save} loading={update.isPending} disabled={!dirty} size="sm">
          <Save size={14} aria-hidden />
          {t("fields.save")}
        </Button>
        {update.isSuccess && !dirty ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-success">
            <Check size={14} aria-hidden />
            {t("fields.saved")}
          </span>
        ) : null}
      </div>
    </div>
  );
}
