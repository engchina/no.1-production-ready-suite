import { Save, Search, Trash2, X } from "lucide-react";
import { useState } from "react";

import {
  Button,
  EmptyState,
  FormGrid,
  FormStatus,
  ListToolbar,
  PAGE_SIZE_OPTIONS,
  RowTitleButton,
  StatusBadge,
  Switch,
  TableSkeleton,
  TextareaField,
  TextField,
  TimedLoadingState,
  toast,
  useConfirm,
  PagedDataTable,
} from "@production-ready/ui";

import {
  api,
  ApiError,
  type JsonValue,
  type RuntimeKnowledgePreviewData,
} from "@/lib/api";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { ErrorState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, requiredTextError } from "@/lib/required-fields";
import { initialLoadError, useEditRuntimeKnowledge, useRuntimeKnowledge } from "@/lib/queries";

type Row = Record<string, JsonValue>;

type FormState = {
  selected: string | null;
  name: string;
  labels: string;
  content: string;
  enabled: boolean;
};

function emptyForm(): FormState {
  return { selected: null, name: "", labels: "", content: "", enabled: true };
}

/** 用語・同義語の表示名（タブ・通知の文言に使う。#682）。 */
export function runtimeKindLabel(): string {
  return t("searchAnswerProfiles.runtime.kind.terms");
}

const ACTIVE_STATUSES = new Set(["", "approved", "stale_review_needed"]);

function text(value: JsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function list(value: JsonValue | undefined): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/** 入力値と編集対象を比べて dirty を判定する。 */
function editableSnapshot({ selected, name, labels, content, enabled }: FormState) {
  return JSON.stringify([selected, name, labels, content, enabled]);
}

function formFromRow(row: Row): FormState {
  const identity = text(row.term);
  return {
    selected: identity,
    name: identity,
    labels: list(row.aliases).join("\n"),
    content: text(row.description),
    enabled: ACTIVE_STATUSES.has(text(row.status)),
  };
}

function rowKey(row: Row): string {
  return text(row.term);
}

type RequiredErrors = { name?: string | null };

/** 保存前の必須の検証（#540）。規則と文言は backend（rag_engine の edit_knowledge）と同じで、「用語」が必須。 */
export function runtimeKnowledgeRequiredErrors(form: FormState): RequiredErrors {
  return { name: requiredTextError(form.name, t("searchAnswerProfiles.runtime.error.termRequired")) };
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

/** 検索・回答プロファイルの用語・同義語（別名・説明）。回答フローで使う（#682）。 */
export function RuntimeKnowledgeManager({ searchAnswerProfileId }: { searchAnswerProfileId: string }) {
  const query = useRuntimeKnowledge(searchAnswerProfileId);
  const save = useEditRuntimeKnowledge(searchAnswerProfileId);
  const confirm = useConfirm();
  const [form, setForm] = useState<FormState>(() => emptyForm());
  // 読み込んだ行（または空の新規）を基準に、未保存の入力だけを離脱ガードの対象にする。
  const [baseline, setBaseline] = useState<FormState>(() => emptyForm());
  const kindLabel = runtimeKindLabel();
  const enabledLabel = t("searchAnswerProfiles.runtime.enabledLabel");
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
    // 直した欄のエラーだけを消す。
    setErrors((current) =>
      Object.fromEntries(Object.entries(current).filter(([field]) => !(field in patch))),
    );
  };

  // 保存のボタンは押せる状態のまま、未入力は押したときに欄の直下へ出す（UX 契約 messaging.md §3.2.1。#541）。
  const saveForm = () => {
    const nextErrors = runtimeKnowledgeRequiredErrors(form);
    setErrors(nextErrors);
    if (focusFirstInvalidField([["runtime-knowledge-name", nextErrors.name]] as const)) return;
    submit();
  };

  const submit = (remove = false) =>
    save.mutate(
      {
        selected: form.selected,
        name: form.name.trim(),
        labels: form.labels,
        content: form.content,
        enabled: form.enabled,
        delete: remove,
      },
      {
        onSuccess: () => {
          load(emptyForm());
          toast.success(
            t(remove ? "searchAnswerProfiles.runtime.deleted" : "searchAnswerProfiles.runtime.saved", {
              kind: kindLabel,
            }),
          );
        },
        onError: (error) =>
          toast.error(
            errorMessage(error, t("searchAnswerProfiles.runtime.saveError", { kind: kindLabel })),
          ),
      },
    );

  // variables は完了後も残るため、isPending のときだけ読む。
  const deletePending = save.isPending && save.variables?.delete === true;
  const savePending = save.isPending && !deletePending;

  const confirmDelete = async () => {
    const ok = await confirm({
      title: t("searchAnswerProfiles.runtime.deleteConfirm.title", { kind: kindLabel }),
      description: t("searchAnswerProfiles.runtime.deleteConfirm.description", {
        name: form.selected ?? "",
      }),
      confirmLabel: t("searchAnswerProfiles.faq.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (ok) submit(true);
  };

  const runPreview = async () => {
    setPreviewing(true);
    try {
      setPreview(
        await api.previewRuntimeKnowledge(searchAnswerProfileId, question.trim()),
      );
    } catch (error) {
      toast.error(errorMessage(error, t("searchAnswerProfiles.runtime.previewError")));
    } finally {
      setPreviewing(false);
    }
  };

  const rows = query.data?.terms ?? [];
  const loadError = initialLoadError(query);
  const paging = useClientListPaging("runtimeKnowledge.listPaging", `${searchAnswerProfileId}:terms`);
  const table = query.isPending ? (
    <TimedLoadingState
      label={t("searchAnswerProfiles.runtime.loading")}
      operationKey={`runtime-knowledge-${searchAnswerProfileId}`}
      testId="runtime-knowledge-loading"
    >
      <TableSkeleton columns={3} rows={{ base: 3, md: 5 }} />
    </TimedLoadingState>
  ) : loadError ? (
    // 初回の読み込みの失敗は、空（0 件）と見せずに失敗と再試行を出す。
    <ErrorState
      message={errorMessage(loadError, t("searchAnswerProfiles.runtime.loadError"))}
      onRetry={() => void query.refetch()}
    />
  ) : (
    <PagedDataTable<Row>
      // 0 件・1 ページでもページ送りを出す例外（#3719。page-archetypes.md「基準から外す例外」）。
      showSinglePagePagination
      paginationLabels={paginationLabels()}
      {...paging}
      pageSizeOptions={PAGE_SIZE_OPTIONS}
      paginationAriaLabel={t("searchAnswerProfiles.runtime.termsPaginationLabel")}
      columns={[
        {
          key: "name",
          header: t("searchAnswerProfiles.runtime.term"),
          rowHeader: true,
          // 名前のボタンと行のクリックで編集フォームへ読み込む（page-archetypes.md §0-7）。
          render: (row) => (
            <RowTitleButton
              title={rowKey(row)}
              current={form.selected === rowKey(row)}
              aria-label={t("searchAnswerProfiles.runtime.editNamed", { name: rowKey(row) })}
              onClick={() => load(formFromRow(row))}
            />
          ),
        },
        {
          key: "labels",
          header: t("searchAnswerProfiles.runtime.aliases"),
          render: (row) => <span className="break-words">{list(row.aliases).join("、")}</span>,
        },
        {
          key: "status",
          header: t("searchAnswerProfiles.runtime.status"),
          render: (row) =>
            ACTIVE_STATUSES.has(text(row.status)) ? (
              <StatusBadge
                variant="success"
                label={t("searchAnswerProfiles.runtime.enabled")}
              />
            ) : (
              <StatusBadge
                variant="neutral"
                label={t("searchAnswerProfiles.runtime.disabled")}
              />
            ),
        },
      ]}
      rows={rows}
      getRowKey={rowKey}
      onRowClick={(row) => load(formFromRow(row))}
      selectedRowKey={form.selected}
      dense
      empty={<EmptyState title={t("searchAnswerProfiles.runtime.empty")} />}
      scrollAriaLabel={t("searchAnswerProfiles.runtime.termsScrollLabel")}
      scrollTestId="runtime-knowledge-terms-scroll-region"
      paginationTestId="runtime-knowledge-terms-pagination"
    />
  );

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed text-fg-muted">
        {t("searchAnswerProfiles.runtime.terms.hint")}
      </p>
      {/* 件数は一覧の上の右に 1 か所（ListCount。#1891）。 */}
      <ListToolbar
        count={query.data ? listCountProps({ matchedTotal: rows.length, filtered: false, live: true }) : undefined}
        testId="runtime-knowledge-terms-toolbar"
      />
      {table}

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {form.selected
              ? t("searchAnswerProfiles.runtime.editTitle", { name: form.selected })
              : t("searchAnswerProfiles.runtime.addTitle", { kind: kindLabel })}
          </h4>
          {/* 編集の欄（#1891）: 用語は名前の幅（md）、別名・説明は文章（全幅）。 */}
          <FormGrid columns={1}>
            <TextField
              id="runtime-knowledge-name"
              width="md"
              label={t("searchAnswerProfiles.runtime.term")}
              value={form.name}
              maxLength={160}
              onChange={(event) => update({ name: event.target.value })}
              error={errors.name ?? undefined}
              required
            />
            <TextareaField
              id="runtime-knowledge-labels"
              label={t("searchAnswerProfiles.runtime.aliasesInput")}
              value={form.labels}
              onChange={(event) => update({ labels: event.target.value })}
              rows={3}
              width="full"
            />
            <TextareaField
              id="runtime-knowledge-content"
              label={t("searchAnswerProfiles.runtime.description")}
              value={form.content}
              onChange={(event) => update({ content: event.target.value })}
              rows={4}
              width="full"
            />
          </FormGrid>
          <div className="flex items-center justify-between gap-3">
            <span className="text-sm text-fg">{enabledLabel}</span>
            <Switch
              checked={form.enabled}
              aria-label={enabledLabel}
              onCheckedChange={(checked) => update({ enabled: checked })}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            {/* 保存と削除は同じ mutation（delete で区別）。スピナーは押した側だけが出し、他は無効にする（#819）。 */}
            <Button
              size="sm"
              icon={Save}
              loading={savePending}
              disabled={deletePending}
              onClick={saveForm}
            >
              {t("searchAnswerProfiles.runtime.save")}
            </Button>
            {form.selected ? (
              <>
                <Button
                  size="sm"
                  variant="ghost"
                  icon={X}
                  disabled={save.isPending}
                  onClick={() => load(emptyForm())}
                >
                  {t("searchAnswerProfiles.runtime.cancel")}
                </Button>
                {/* 確認ダイアログを開く起点。確定は ConfirmDialog の danger ボタンで行う。
                    保存（主操作）の隣に置かず、操作行の反対の端に ghost + tone="danger" で置く（README §4 カード内の操作行）。 */}
                <Button
                  size="sm"
                  variant="ghost"
                  tone="danger"
                  icon={Trash2}
                  className="sm:ml-auto"
                  loading={deletePending}
                  disabled={savePending}
                  onClick={() => void confirmDelete()}
                >
                  {t("searchAnswerProfiles.faq.delete")}
                </Button>
              </>
            ) : null}
          </div>
        </section>

        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {t("searchAnswerProfiles.runtime.previewTitle")}
          </h4>
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("searchAnswerProfiles.runtime.previewHelp.terms")}
          </p>
          <TextField
            id="runtime-knowledge-question"
            // 質問の文は文章（全幅。#1891）。
            width="full"
            label={t("searchAnswerProfiles.runtime.previewQuestion")}
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
            {t("searchAnswerProfiles.runtime.previewRun")}
          </Button>
          {preview ? (
            <dl className="space-y-1 text-xs">
              <div>
                <dt className="font-medium text-fg">{t("searchAnswerProfiles.runtime.matchedTerms")}</dt>
                <dd className="break-words text-fg-muted">{preview.matched_terms.join("、") || "—"}</dd>
              </div>
              <div>
                <dt className="font-medium text-fg">
                  {t("searchAnswerProfiles.runtime.expanded")}
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
                t("searchAnswerProfiles.runtime.saveError"),
              )}
            />
          ) : null}
        </section>
      </div>
    </div>
  );
}
