import { Pencil, Save, Search, Trash2, X } from "lucide-react";
import { useState } from "react";

import {
  Button,
  DataTable,
  EmptyState,
  FormStatus,
  SelectField,
  StatusBadge,
  Switch,
  TextField,
  toast,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";

import {
  api,
  ApiError,
  type JsonValue,
  type RuntimeKnowledgeKind,
  type RuntimeKnowledgePreviewData,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useEditRuntimeKnowledge, useRuntimeKnowledge } from "@/lib/queries";

type Row = Record<string, JsonValue>;

type FormState = {
  kind: RuntimeKnowledgeKind;
  selected: string | null;
  name: string;
  title: string;
  labels: string;
  content: string;
  enabled: boolean;
};

const EMPTY_FORM: FormState = {
  kind: "terms",
  selected: null,
  name: "",
  title: "",
  labels: "",
  content: "",
  enabled: true,
};

const KIND_OPTIONS: SelectFieldOption<RuntimeKnowledgeKind>[] = [
  { value: "terms", label: t("businessViews.runtime.kind.terms") },
  { value: "rules", label: t("businessViews.runtime.kind.rules") },
];

const ACTIVE_STATUSES = new Set(["", "approved", "stale_review_needed"]);

function text(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function list(value: JsonValue | undefined): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/** 種類の切替だけでは dirty にしない（入力値と編集対象を比べる）。 */
function editableSnapshot({ selected, name, title, labels, content, enabled }: FormState) {
  return JSON.stringify([selected, name, title, labels, content, enabled]);
}

function formFromRow(kind: RuntimeKnowledgeKind, row: Row): FormState {
  const identity = kind === "terms" ? text(row.term) : text(row.id);
  return {
    kind,
    selected: identity,
    name: identity,
    title: text(row.title),
    labels: list(kind === "terms" ? row.aliases : row.triggers).join("\n"),
    content: text(kind === "terms" ? row.description : row.content),
    enabled: ACTIVE_STATUSES.has(text(row.status)),
  };
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

/** 業務ビューの用語(別名・説明)とルール(照合キーワード・内容)。DocRAG 回答エンジンで使う。 */
export function RuntimeKnowledgeManager({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const query = useRuntimeKnowledge(businessViewId);
  const save = useEditRuntimeKnowledge(businessViewId);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  // 読み込んだ行（または空の新規）を基準に、未保存の入力だけを離脱ガードの対象にする。
  const [baseline, setBaseline] = useState<FormState>(EMPTY_FORM);
  const load = (next: FormState) => {
    setForm(next);
    setBaseline(next);
  };
  useLeaveGuard(editableSnapshot(form) !== editableSnapshot(baseline));
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<RuntimeKnowledgePreviewData | null>(
    null,
  );
  const [previewing, setPreviewing] = useState(false);
  const update = (patch: Partial<FormState>) =>
    setForm((current) => ({ ...current, ...patch }));

  const submit = (remove = false) =>
    save.mutate(
      {
        kind: form.kind,
        selected: form.selected,
        name: form.name.trim(),
        title: form.title.trim(),
        labels: form.labels,
        content: form.content,
        enabled: form.enabled,
        delete: remove,
      },
      {
        onSuccess: () => {
          load({ ...EMPTY_FORM, kind: form.kind });
          toast.success(
            t(
              remove
                ? "businessViews.runtime.deleted"
                : "businessViews.runtime.saved",
            ),
          );
        },
        onError: (error) =>
          toast.error(
            errorMessage(error, t("businessViews.runtime.saveError")),
          ),
      },
    );

  const runPreview = async () => {
    setPreviewing(true);
    try {
      setPreview(
        await api.previewRuntimeKnowledge(businessViewId, question.trim()),
      );
    } catch (error) {
      toast.error(errorMessage(error, t("businessViews.runtime.previewError")));
    } finally {
      setPreviewing(false);
    }
  };

  const tableFor = (kind: RuntimeKnowledgeKind, rows: Row[]) => (
    <DataTable<Row>
      columns={[
        {
          key: "name",
          header: t(
            kind === "terms"
              ? "businessViews.runtime.term"
              : "businessViews.runtime.ruleTitle",
          ),
          rowHeader: true,
          render: (row) => (
            <span className="break-words">
              {kind === "terms"
                ? text(row.term)
                : text(row.title) || text(row.id)}
            </span>
          ),
        },
        {
          key: "labels",
          header: t(
            kind === "terms"
              ? "businessViews.runtime.aliases"
              : "businessViews.runtime.triggers",
          ),
          render: (row) => (
            <span className="break-words">
              {list(kind === "terms" ? row.aliases : row.triggers).join("、")}
            </span>
          ),
        },
        {
          key: "status",
          header: t("businessViews.runtime.status"),
          render: (row) =>
            ACTIVE_STATUSES.has(text(row.status)) ? (
              <StatusBadge
                variant="success"
                label={t("businessViews.runtime.enabled")}
              />
            ) : (
              <StatusBadge
                variant="neutral"
                label={t("businessViews.runtime.disabled")}
              />
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
              icon={Pencil}
              onClick={() => load(formFromRow(kind, row))}
            >
              {t("businessViews.runtime.edit")}
            </Button>
          ),
        },
      ]}
      rows={rows}
      getRowKey={(row) => (kind === "terms" ? text(row.term) : text(row.id))}
      loading={query.isPending}
      dense
      empty={<EmptyState title={t("businessViews.runtime.empty")} />}
    />
  );

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <h4 className="text-sm font-semibold text-fg">
          {t("businessViews.runtime.kind.terms")}
        </h4>
        {tableFor("terms", query.data?.terms ?? [])}
      </section>
      <section className="space-y-2">
        <h4 className="text-sm font-semibold text-fg">
          {t("businessViews.runtime.kind.rules")}
        </h4>
        {tableFor("rules", query.data?.rules ?? [])}
      </section>

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {form.selected
              ? t("businessViews.runtime.editTitle", { name: form.selected })
              : t("businessViews.runtime.addTitle")}
          </h4>
          <SelectField
            id="runtime-knowledge-kind"
            label={t("businessViews.runtime.kindLabel")}
            value={form.kind}
            options={KIND_OPTIONS}
            onValueChange={(value) =>
              value && update({ kind: value, selected: null })
            }
          />
          <TextField
            id="runtime-knowledge-name"
            label={t(
              form.kind === "terms"
                ? "businessViews.runtime.term"
                : "businessViews.runtime.ruleId",
            )}
            value={form.name}
            maxLength={160}
            onChange={(event) => update({ name: event.target.value })}
          />
          {form.kind === "rules" ? (
            <TextField
              id="runtime-knowledge-title"
              label={t("businessViews.runtime.ruleTitle")}
              value={form.title}
              maxLength={160}
              onChange={(event) => update({ title: event.target.value })}
            />
          ) : null}
          {(["labels", "content"] as const).map((field) => (
            <div key={field}>
              <label
                htmlFor={`runtime-knowledge-${field}`}
                className="text-sm font-medium text-fg"
              >
                {t(
                  field === "labels"
                    ? form.kind === "terms"
                      ? "businessViews.runtime.aliasesInput"
                      : "businessViews.runtime.triggersInput"
                    : form.kind === "terms"
                      ? "businessViews.runtime.description"
                      : "businessViews.runtime.content",
                )}
              </label>
              <textarea
                id={`runtime-knowledge-${field}`}
                value={form[field]}
                onChange={(event) => update({ [field]: event.target.value })}
                rows={field === "labels" ? 3 : 4}
                className="mt-1 w-full rounded-md border border-border-control bg-surface-sunken px-3 py-2 text-sm outline-none focus-visible:border-focus-ring"
              />
            </div>
          ))}
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg">
              {t("businessViews.runtime.enabledLabel")}
            </span>
            <Switch
              checked={form.enabled}
              aria-label={t("businessViews.runtime.enabledLabel")}
              onCheckedChange={(checked) => update({ enabled: checked })}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              icon={Save}
              loading={save.isPending}
              disabled={!form.name.trim()}
              onClick={() => submit()}
            >
              {t("businessViews.runtime.save")}
            </Button>
            {form.selected ? (
              <>
                <Button
                  size="sm"
                  variant="danger"
                  icon={Trash2}
                  disabled={save.isPending}
                  onClick={() => submit(true)}
                >
                  {t("businessViews.faq.delete")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={X}
                  onClick={() => load({ ...EMPTY_FORM, kind: form.kind })}
                >
                  {t("businessViews.runtime.cancel")}
                </Button>
              </>
            ) : null}
          </div>
        </section>

        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {t("businessViews.runtime.previewTitle")}
          </h4>
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("businessViews.runtime.previewHelp")}
          </p>
          <TextField
            id="runtime-knowledge-question"
            label={t("businessViews.runtime.previewQuestion")}
            value={question}
            maxLength={2000}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <Button
            size="sm"
            variant="secondary"
            icon={Search}
            loading={previewing}
            disabled={!question.trim()}
            onClick={() => void runPreview()}
          >
            {t("businessViews.runtime.previewRun")}
          </Button>
          {preview ? (
            <dl className="space-y-1 text-xs">
              <div>
                <dt className="font-medium text-fg">
                  {t("businessViews.runtime.matchedTerms")}
                </dt>
                <dd className="break-words text-fg-muted">
                  {preview.matched_terms.join("、") || "—"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-fg">
                  {t("businessViews.runtime.matchedRules")}
                </dt>
                <dd className="break-words text-fg-muted">
                  {preview.matched_rules.join("、") || "—"}
                </dd>
              </div>
              <div>
                <dt className="font-medium text-fg">
                  {t("businessViews.runtime.expanded")}
                </dt>
                <dd className="break-words text-fg-muted">
                  {preview.expanded_question}
                </dd>
              </div>
            </dl>
          ) : null}
          {save.isError ? (
            <FormStatus
              tone="danger"
              message={errorMessage(
                save.error,
                t("businessViews.runtime.saveError"),
              )}
            />
          ) : null}
        </section>
      </div>
    </div>
  );
}
