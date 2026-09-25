import { FileSpreadsheet, Plus, Trash2, Upload } from "lucide-react";
import { useState } from "react";

import {
  Button,
  DataTable,
  EmptyState,
  FormStatus,
  SelectField,
  TextField,
  toast,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";

import {
  api,
  ApiError,
  type ApprovedFaqImportMode,
  type ApprovedFaqImportPreviewData,
  type ApprovedFaqRecordData,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useApprovedFaq, useApprovedFaqMutation } from "@/lib/queries";

const IMPORT_MODE_OPTIONS: SelectFieldOption<ApprovedFaqImportMode>[] = [
  { value: "INSERT", label: t("businessViews.faq.importMode.INSERT") },
  {
    value: "DELETE_THEN_INSERT",
    label: t("businessViews.faq.importMode.DELETE_THEN_INSERT"),
  },
];

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

/** 業務ビューの Approved FAQ(類似問)の一覧・追加・削除・Excel 取込。 */
export function ApprovedFaqManager({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const query = useApprovedFaq(businessViewId);
  const records = query.data?.records ?? [];
  const add = useApprovedFaqMutation(
    businessViewId,
    (body: { question: string; answer: string }) =>
      api.addApprovedFaq(businessViewId, body),
  );
  const remove = useApprovedFaqMutation(businessViewId, (ids: string[]) =>
    api.deleteApprovedFaq(businessViewId, ids),
  );
  const importFaq = useApprovedFaqMutation(
    businessViewId,
    (args: { file: File; mode: ApprovedFaqImportMode }) =>
      api.importApprovedFaq(businessViewId, args.file, args.mode),
  );
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<ApprovedFaqImportMode>("INSERT");
  const [preview, setPreview] = useState<ApprovedFaqImportPreviewData | null>(
    null,
  );
  const [previewError, setPreviewError] = useState("");

  const selectFile = async (next: File | null) => {
    setFile(next);
    setPreview(null);
    setPreviewError("");
    if (!next) return;
    try {
      setPreview(await api.previewApprovedFaqImport(businessViewId, next));
    } catch (error) {
      setPreviewError(errorMessage(error, t("businessViews.faq.previewError")));
    }
  };

  return (
    <div className="space-y-5">
      <DataTable<ApprovedFaqRecordData>
        columns={[
          {
            key: "question",
            header: t("businessViews.faq.question"),
            rowHeader: true,
            render: (row) => (
              <span className="break-words">{row.question}</span>
            ),
          },
          {
            key: "answer",
            header: t("businessViews.faq.answer"),
            render: (row) => (
              <span className="line-clamp-3 whitespace-pre-wrap break-words">
                {row.answer}
              </span>
            ),
          },
          {
            key: "actions",
            header: t("businessViews.faq.actions"),
            align: "right",
            render: (row) => (
              <Button
                size="sm"
                variant="ghost"
                icon={Trash2}
                loading={remove.isPending && remove.variables?.includes(row.id)}
                onClick={() =>
                  remove.mutate([row.id], {
                    onSuccess: () =>
                      toast.success(t("businessViews.faq.deleted")),
                    onError: (error) =>
                      toast.error(
                        errorMessage(error, t("businessViews.faq.saveError")),
                      ),
                  })
                }
              >
                {t("businessViews.faq.delete")}
              </Button>
            ),
          },
        ]}
        rows={records}
        getRowKey={(row) => row.id}
        loading={query.isPending}
        dense
        empty={
          <EmptyState
            title={t("businessViews.faq.empty")}
            hint={t("businessViews.faq.emptyHint")}
          />
        }
      />

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {t("businessViews.faq.addTitle")}
          </h4>
          <TextField
            id="approved-faq-question"
            label={t("businessViews.faq.question")}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            maxLength={1000}
          />
          <div>
            <label
              htmlFor="approved-faq-answer"
              className="text-sm font-medium text-fg"
            >
              {t("businessViews.faq.answer")}
            </label>
            <textarea
              id="approved-faq-answer"
              value={answer}
              onChange={(event) => setAnswer(event.target.value)}
              rows={4}
              maxLength={20000}
              className="mt-1 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring"
            />
          </div>
          <Button
            size="sm"
            icon={Plus}
            loading={add.isPending}
            disabled={!question.trim() || !answer.trim()}
            onClick={() =>
              add.mutate(
                { question: question.trim(), answer: answer.trim() },
                {
                  onSuccess: () => {
                    setQuestion("");
                    setAnswer("");
                    toast.success(t("businessViews.faq.added"));
                  },
                  onError: (error) =>
                    toast.error(
                      errorMessage(error, t("businessViews.faq.saveError")),
                    ),
                },
              )
            }
          >
            {t("businessViews.faq.add")}
          </Button>
        </section>

        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
            <FileSpreadsheet size={16} aria-hidden />
            {t("businessViews.faq.importTitle")}
          </h4>
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("businessViews.faq.importHelp")}
          </p>
          <input
            type="file"
            accept=".xlsx,.xls"
            aria-label={t("businessViews.faq.importFile")}
            onChange={(event) =>
              void selectFile(event.target.files?.[0] ?? null)
            }
            className="block w-full text-sm text-fg file:mr-3 file:rounded-md file:border file:border-border-control file:bg-surface file:px-3 file:py-1.5 file:text-sm"
          />
          <SelectField
            id="approved-faq-import-mode"
            label={t("businessViews.faq.importModeLabel")}
            value={mode}
            options={IMPORT_MODE_OPTIONS}
            onValueChange={(value) => value && setMode(value)}
          />
          {previewError ? (
            <FormStatus tone="danger" message={previewError} />
          ) : null}
          {preview ? (
            <div className="space-y-1 text-xs text-fg-muted">
              <p>
                {t("businessViews.faq.previewCount", { count: preview.total })}
              </p>
              <ul className="space-y-1">
                {preview.rows.map((row) => (
                  <li key={row.row} className="break-words">
                    {row.question}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            icon={Upload}
            loading={importFaq.isPending}
            disabled={!file || !preview}
            onClick={() =>
              file &&
              importFaq.mutate(
                { file, mode },
                {
                  onSuccess: (data) =>
                    toast.success(
                      t("businessViews.faq.imported", {
                        inserted: data.inserted_count,
                        deleted: data.deleted_count,
                      }),
                    ),
                  onError: (error) =>
                    toast.error(
                      errorMessage(error, t("businessViews.faq.saveError")),
                    ),
                },
              )
            }
          >
            {t("businessViews.faq.import")}
          </Button>
        </section>
      </div>
    </div>
  );
}
