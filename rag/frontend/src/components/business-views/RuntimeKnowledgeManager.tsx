import { Save, Search, Trash2, X } from "lucide-react";
import { useState } from "react";

import {
  Button,
  EmptyState,
  FormStatus,
  RowTitleButton,
  SelectField,
  StatusBadge,
  Switch,
  TableSkeleton,
  TextareaField,
  TextField,
  TimedLoadingState,
  toast,
  useConfirm,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";

import {
  api,
  ApiError,
  type JsonValue,
  type RuntimeKnowledgeKind,
  type RuntimeKnowledgePreviewData,
} from "@/lib/api";
import { PagedDataTable } from "@/components/PagedDataTable";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, requiredTextError } from "@/lib/required-fields";
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

function rowKey(kind: RuntimeKnowledgeKind, row: Row): string {
  return kind === "terms" ? text(row.term) : text(row.id);
}

function rowName(kind: RuntimeKnowledgeKind, row: Row): string {
  return kind === "terms" ? text(row.term) : text(row.title) || text(row.id);
}

type RequiredField = "name" | "title" | "content";
type RequiredErrors = Partial<Record<RequiredField, string | null>>;

const FIELD_IDS: Record<RequiredField, string> = {
  name: "runtime-knowledge-name",
  title: "runtime-knowledge-title",
  content: "runtime-knowledge-content",
};

/**
 * 保存前の必須の検証（#540）。規則と文言は backend（docrag_core の edit_knowledge）と同じ:
 * 用語は「用語」、ルールは「ルール ID」「ルール名」「ルール内容」が必須。
 */
export function runtimeKnowledgeRequiredErrors(form: FormState): RequiredErrors {
  if (form.kind === "terms") {
    return { name: requiredTextError(form.name, t("businessViews.runtime.error.termRequired")) };
  }
  return {
    name: requiredTextError(form.name, t("businessViews.runtime.error.ruleIdRequired")),
    title: requiredTextError(form.title, t("businessViews.runtime.error.ruleTitleRequired")),
    content: requiredTextError(form.content, t("businessViews.runtime.error.ruleContentRequired")),
  };
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

/** 業務ビューの用語(別名・説明)とルール(照合キーワード・内容)。回答フローで使う。 */
export function RuntimeKnowledgeManager({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const query = useRuntimeKnowledge(businessViewId);
  const save = useEditRuntimeKnowledge(businessViewId);
  const confirm = useConfirm();
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  // 読み込んだ行（または空の新規）を基準に、未保存の入力だけを離脱ガードの対象にする。
  const [baseline, setBaseline] = useState<FormState>(EMPTY_FORM);
  const load = (next: FormState) => {
    setForm(next);
    setBaseline(next);
    setErrors({});
  };
  useLeaveGuard(editableSnapshot(form) !== editableSnapshot(baseline));
  const [question, setQuestion] = useState("");
  const [preview, setPreview] = useState<RuntimeKnowledgePreviewData | null>(
    null,
  );
  const [previewing, setPreviewing] = useState(false);
  const [errors, setErrors] = useState<RequiredErrors>({});
  const update = (patch: Partial<FormState>) => {
    setForm((current) => ({ ...current, ...patch }));
    // 直した欄のエラーだけを消す。種類を切り替えたときは必須の欄が変わるので全部消す。
    setErrors((current) =>
      "kind" in patch
        ? {}
        : Object.fromEntries(Object.entries(current).filter(([field]) => !(field in patch))),
    );
  };

  // 保存のボタンは押せる状態のまま、未入力は押したときに欄の直下へ出す（UX 契約 messaging.md §3.2.1。#541）。
  const saveForm = () => {
    const nextErrors = runtimeKnowledgeRequiredErrors(form);
    setErrors(nextErrors);
    const order: RequiredField[] = ["name", "title", "content"];
    if (focusFirstInvalidField(order.map((field) => [FIELD_IDS[field], nextErrors[field]] as const))) return;
    submit();
  };

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

  const confirmDelete = async () => {
    const ok = await confirm({
      title: t("businessViews.runtime.deleteConfirm.title"),
      description: t("businessViews.runtime.deleteConfirm.description", {
        name: form.selected ?? "",
      }),
      confirmLabel: t("businessViews.faq.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (ok) submit(true);
  };

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

  const tableFor = (kind: RuntimeKnowledgeKind, rows: Row[]) =>
    query.isPending ? (
      // 用語と規則は同じ 1 回の取得。経過時間は用語の表の位置だけに出し、規則は形だけにする（messaging.md §3.7）。
      kind === "terms" ? (
        <TimedLoadingState
          label={t("businessViews.runtime.loading")}
          operationKey={`runtime-knowledge-${businessViewId}`}
          testId="runtime-knowledge-loading"
        >
          <TableSkeleton columns={3} rows={{ base: 3, md: 5 }} />
        </TimedLoadingState>
      ) : (
        <TableSkeleton columns={3} rows={{ base: 3, md: 5 }} />
      )
    ) : (
    <PagedDataTable<Row>
      columns={[
        {
          key: "name",
          header: t(
            kind === "terms"
              ? "businessViews.runtime.term"
              : "businessViews.runtime.ruleTitle",
          ),
          rowHeader: true,
          // 名前のボタンと行のクリックで編集フォームへ読み込む（page-archetypes.md §0-7）。
          render: (row) => (
            <RowTitleButton
              title={rowName(kind, row)}
              current={form.kind === kind && form.selected === rowKey(kind, row)}
              aria-label={t("businessViews.runtime.editNamed", {
                name: rowName(kind, row),
              })}
              onClick={() => load(formFromRow(kind, row))}
            />
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
      ]}
      rows={rows}
      getRowKey={(row) => rowKey(kind, row)}
      onRowClick={(row) => load(formFromRow(kind, row))}
      selectedRowKey={form.kind === kind ? form.selected : null}
      resetKey={businessViewId}
      dense
      empty={<EmptyState title={t("businessViews.runtime.empty")} />}
      scrollAriaLabel={t(
        kind === "terms"
          ? "businessViews.runtime.termsScrollLabel"
          : "businessViews.runtime.rulesScrollLabel",
      )}
      scrollTestId={`runtime-knowledge-${kind}-scroll-region`}
      paginationTestId={`runtime-knowledge-${kind}-pagination`}
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
            error={errors.name ?? undefined}
            required
          />
          {form.kind === "rules" ? (
            <TextField
              id="runtime-knowledge-title"
              label={t("businessViews.runtime.ruleTitle")}
              value={form.title}
              maxLength={160}
              onChange={(event) => update({ title: event.target.value })}
              error={errors.title ?? undefined}
              required
            />
          ) : null}
          {(["labels", "content"] as const).map((field) => {
            // ルール内容だけが必須（用語の説明・別名・照合キーワードは任意。backend と同じ）。
            const required = field === "content" && form.kind === "rules";
            const error = field === "content" ? errors.content : null;
            return (
              <TextareaField
                key={field}
                id={`runtime-knowledge-${field}`}
                required={required}
                label={t(
                  field === "labels"
                    ? form.kind === "terms"
                      ? "businessViews.runtime.aliasesInput"
                      : "businessViews.runtime.triggersInput"
                    : form.kind === "terms"
                      ? "businessViews.runtime.description"
                      : "businessViews.runtime.content",
                )}
                error={error ?? undefined}
                value={form[field]}
                onChange={(event) => update({ [field]: event.target.value })}
                rows={field === "labels" ? 3 : 4}
              />
            );
          })}
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
              onClick={saveForm}
            >
              {t("businessViews.runtime.save")}
            </Button>
            {form.selected ? (
              <>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={X}
                  onClick={() => load({ ...EMPTY_FORM, kind: form.kind })}
                >
                  {t("businessViews.runtime.cancel")}
                </Button>
                {/* 確認ダイアログを開く起点。確定は ConfirmDialog の danger ボタンで行う。
                    保存（主操作）の隣に置かず、操作行の反対の端に ghost + tone="danger" で置く（README §4 カード内の操作行）。 */}
                <Button
                  size="sm"
                  variant="ghost"
                  tone="danger"
                  icon={Trash2}
                  className="sm:ml-auto"
                  disabled={save.isPending}
                  onClick={() => void confirmDelete()}
                >
                  {t("businessViews.faq.delete")}
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
            required
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
