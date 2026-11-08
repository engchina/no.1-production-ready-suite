import { CheckCircle2, FileSpreadsheet, ListChecks, Plus, Trash2, Upload } from "lucide-react";
import { useMemo, useState } from "react";

import {
  Button,
  EmptyState,
  FieldLabel,
  FormGrid,
  FormStatus,
  ListToolbar,
  PAGE_SIZE_OPTIONS,
  ProcessingIndicator,
  RowActionMenu,
  SearchableMultiSelect,
  SelectField,
  SideSheet,
  StatusBadge,
  TableSkeleton,
  TextareaField,
  TextField,
  Switch,
  TimedLoadingState,
  toast,
  useConfirm,
  type SearchableSelectOption,
  type SelectFieldOption,
  PagedDataTable,
} from "@production-ready/ui";

import {
  api,
  ApiError,
  type ApprovedFaqImportMode,
  type ApprovedFaqImportPreviewData,
  type ApprovedFaqKnowledgeBaseData,
  type ApprovedFaqRecordData,
  type ApprovedFaqReviewReason,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { ErrorState } from "@/components/StateViews";
import { knowledgeBaseMultiSelectLabels } from "@/components/knowledge-bases/KnowledgeBaseMultiSelect";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { focusFirstInvalidField, requiredTextError } from "@/lib/required-fields";
import { initialLoadError, useApprovedFaq, useApprovedFaqMutation } from "@/lib/queries";

// 停止（要確認）の理由の文（#4621）。
const REVIEW_REASON_KEYS: Record<ApprovedFaqReviewReason, I18nKey> = {
  no_target_knowledge_bases: "searchAnswerProfiles.faq.reason.no_target_knowledge_bases",
  knowledge_base_removed: "searchAnswerProfiles.faq.reason.knowledge_base_removed",
  source_document_missing: "searchAnswerProfiles.faq.reason.source_document_missing",
};

/** 対象の KB の名前（プロファイルから外れた KB は印を付ける。引けない ID はそのまま）。 */
function targetNames(ids: readonly string[], knowledgeBases: readonly ApprovedFaqKnowledgeBaseData[]) {
  const byId = new Map(knowledgeBases.map((kb) => [kb.id, kb]));
  return ids
    .map((id) => {
      const kb = byId.get(id);
      if (!kb) return id;
      return kb.in_profile
        ? kb.name
        : t("searchAnswerProfiles.faq.targets.removedName", { name: kb.name });
    })
    .join("、");
}

/** FAQ の根拠の状態・対象・承認（質問の下に出す。状態は文字でも示す）。 */
function FaqScopeSummary({
  row,
  knowledgeBases,
}: {
  row: ApprovedFaqRecordData;
  knowledgeBases: readonly ApprovedFaqKnowledgeBaseData[];
}) {
  const needsReview = row.review_reasons.length > 0;
  return (
    <div className="mt-1 space-y-1 text-xs leading-relaxed text-fg-muted">
      {needsReview || row.source_updated ? (
        <div className="flex flex-wrap gap-1.5">
          {needsReview ? (
            <StatusBadge variant="warning" label={t("searchAnswerProfiles.faq.state.needsReview")} />
          ) : null}
          {row.source_updated ? (
            <StatusBadge variant="info" label={t("searchAnswerProfiles.faq.state.sourceUpdated")} />
          ) : null}
        </div>
      ) : null}
      {row.review_reasons.map((reason) => (
        <p key={reason}>{t(REVIEW_REASON_KEYS[reason])}</p>
      ))}
      {row.source_updated && !needsReview ? (
        <p>{t("searchAnswerProfiles.faq.sourceUpdatedHelp")}</p>
      ) : null}
      <p className="break-words" data-testid={`approved-faq-targets-${row.id}`}>
        {row.target_knowledge_base_ids.length
          ? t("searchAnswerProfiles.faq.targets.summary", {
              names: targetNames(row.target_knowledge_base_ids, knowledgeBases),
            })
          : t("searchAnswerProfiles.faq.targets.none")}
      </p>
      <p data-testid={`approved-faq-approval-${row.id}`}>
        {row.approved_at
          ? t("searchAnswerProfiles.faq.approval.summary", {
              name: row.approved_by ?? t("searchAnswerProfiles.faq.approval.unknownUser"),
              time: formatDateTime(row.approved_at),
            })
          : t("searchAnswerProfiles.faq.approval.none")}
      </p>
    </div>
  );
}

const IMPORT_MODE_OPTIONS: SelectFieldOption<ApprovedFaqImportMode>[] = [
  { value: "INSERT", label: t("searchAnswerProfiles.faq.importMode.INSERT") },
  {
    value: "DELETE_THEN_INSERT",
    label: t("searchAnswerProfiles.faq.importMode.DELETE_THEN_INSERT"),
  },
];

function errorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

/** 検索・回答プロファイルの Approved FAQ(類似問)の一覧・追加・削除・Excel 取込。 */
export function ApprovedFaqManager({
  searchAnswerProfileId,
}: {
  searchAnswerProfileId: string;
}) {
  const query = useApprovedFaq(searchAnswerProfileId);
  const records = query.data?.records ?? [];
  const paging = useClientListPaging("approvedFaqs.listPaging", searchAnswerProfileId);
  // 初回の読み込みの失敗は、空（0 件・既定のオン）と見せずに失敗と再試行を出す。
  const loadError = initialLoadError(query);
  const knowledgeBases = useMemo(() => query.data?.knowledge_bases ?? [], [query.data?.knowledge_bases]);
  // 対象に選べるのはプロファイルの参照 KB だけ。既定はその全部（最も厳しい。#4621）。
  const knowledgeBaseOptions = useMemo<SearchableSelectOption[]>(
    () =>
      knowledgeBases
        .filter((kb) => kb.in_profile)
        .map((kb) => ({ value: kb.id, label: kb.name })),
    [knowledgeBases],
  );
  const allTargets = useMemo(() => knowledgeBaseOptions.map((option) => option.value), [knowledgeBaseOptions]);
  const add = useApprovedFaqMutation(
    searchAnswerProfileId,
    (body: { question: string; answer: string; target_knowledge_base_ids: string[] }) =>
      api.addApprovedFaq(searchAnswerProfileId, body),
  );
  const confirmScope = useApprovedFaqMutation(
    searchAnswerProfileId,
    (args: { faqId: string; targets: string[] }) =>
      api.confirmApprovedFaq(searchAnswerProfileId, args.faqId, args.targets),
  );
  const remove = useApprovedFaqMutation(searchAnswerProfileId, (ids: string[]) =>
    api.deleteApprovedFaq(searchAnswerProfileId, ids),
  );
  const setEnabled = useApprovedFaqMutation(searchAnswerProfileId, (enabled: boolean) =>
    api.setApprovedFaqEnabled(searchAnswerProfileId, enabled),
  );
  const importFaq = useApprovedFaqMutation(
    searchAnswerProfileId,
    (args: { file: File; mode: ApprovedFaqImportMode; targets: string[] }) =>
      api.importApprovedFaq(searchAnswerProfileId, args.file, args.mode, args.targets),
  );
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  // null は既定（プロファイルの全 KB）。利用者が選び直したときだけ値を持つ。
  const [addTargets, setAddTargets] = useState<string[] | null>(null);
  const [importTargets, setImportTargets] = useState<string[] | null>(null);
  const [importTargetsError, setImportTargetsError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<ApprovedFaqRecordData | null>(null);
  const [confirmTargets, setConfirmTargets] = useState<string[]>([]);
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [addErrors, setAddErrors] = useState<{
    question?: string | null;
    answer?: string | null;
    targets?: string | null;
  }>({});
  const effectiveAddTargets = addTargets ?? allTargets;
  const effectiveImportTargets = importTargets ?? allTargets;
  const targetsRequired = t("searchAnswerProfiles.faq.targets.required");

  const openConfirm = (row: ApprovedFaqRecordData) => {
    // 今の対象のうちプロファイルに残る KB を初期値にする（無ければ全 KB）。
    const kept = row.target_knowledge_base_ids.filter((id) => allTargets.includes(id));
    setConfirmTargets(kept.length ? kept : allTargets);
    setConfirmError(null);
    setConfirming(row);
  };
  const submitConfirm = () => {
    if (!confirming) return;
    if (!confirmTargets.length) {
      setConfirmError(targetsRequired);
      return;
    }
    confirmScope.mutate(
      { faqId: confirming.id, targets: confirmTargets },
      {
        onSuccess: () => {
          setConfirming(null);
          toast.success(t("searchAnswerProfiles.faq.confirm.done"));
        },
        onError: (error) =>
          toast.error(errorMessage(error, t("searchAnswerProfiles.faq.confirm.error"))),
      },
    );
  };
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<ApprovedFaqImportMode>("INSERT");
  const [preview, setPreview] = useState<ApprovedFaqImportPreviewData | null>(
    null,
  );
  const [previewError, setPreviewError] = useState("");
  const confirm = useConfirm();

  // FAQ の削除は取り消せないため、行のメニューから確認ダイアログを通す（buttons.md §5.1）。
  const handleDelete = async (row: ApprovedFaqRecordData) => {
    const ok = await confirm({
      title: t("searchAnswerProfiles.faq.deleteConfirm.title"),
      description: t("searchAnswerProfiles.faq.deleteConfirm.description", {
        question: row.question,
      }),
      confirmLabel: t("searchAnswerProfiles.faq.delete"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    remove.mutate([row.id], {
      onSuccess: () => toast.success(t("searchAnswerProfiles.faq.deleted")),
      onError: (error) =>
        toast.error(errorMessage(error, t("searchAnswerProfiles.faq.saveError"))),
    });
  };
  // 追加前の Q&A 入力があるときだけ離脱を確認する。
  useLeaveGuard(Boolean(question.trim() || answer.trim()));

  const selectFile = async (next: File | null) => {
    setFile(next);
    setPreview(null);
    setPreviewError("");
    if (!next) return;
    try {
      setPreview(await api.previewApprovedFaqImport(searchAnswerProfileId, next));
    } catch (error) {
      setPreviewError(errorMessage(error, t("searchAnswerProfiles.faq.previewError")));
    }
  };

  return (
    <div className="space-y-5">
      {/* 類似問の提示のオン / オフ（検索・回答プロファイルごと。既定はオン。#684）。 */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p id="approved-faq-enabled-label" className="text-sm font-medium text-fg">
            {t("searchAnswerProfiles.faq.enabled.label")}
          </p>
          <p id="approved-faq-enabled-help" className="mt-0.5 text-xs leading-relaxed text-fg-muted">
            {t("searchAnswerProfiles.faq.enabled.help")}
          </p>
        </div>
        <Switch
          checked={query.data?.enabled ?? true}
          // 読み込めていない間（読み込み中・失敗）は今の設定が分からないので切り替えさせない。
          disabled={!query.data || setEnabled.isPending}
          onCheckedChange={(checked) =>
            setEnabled.mutate(checked, {
              onError: (error) =>
                toast.error(errorMessage(error, t("searchAnswerProfiles.faq.enabled.error"))),
            })
          }
          aria-labelledby="approved-faq-enabled-label"
          aria-describedby="approved-faq-enabled-help"
          data-testid="approved-faq-enabled"
        />
      </div>
      {/* 件数は一覧の上の右に 1 か所（ListCount。#1891）。 */}
      <ListToolbar
        count={query.data ? listCountProps({ matchedTotal: records.length, filtered: false, live: true }) : undefined}
        testId="approved-faq-toolbar"
      />
      {query.isPending ? (
        <TimedLoadingState
          label={t("searchAnswerProfiles.faq.loading")}
          operationKey={`approved-faq-${searchAnswerProfileId}`}
          testId="approved-faq-loading"
        >
          <TableSkeleton columns={3} />
        </TimedLoadingState>
      ) : loadError ? (
        <ErrorState
          message={errorMessage(loadError, t("searchAnswerProfiles.faq.loadError"))}
          onRetry={() => void query.refetch()}
        />
      ) : (
      <PagedDataTable<ApprovedFaqRecordData>
        // 0 件・1 ページでもページ送りを出す例外（#3719。page-archetypes.md「基準から外す例外」）。
        showSinglePagePagination
        paginationLabels={paginationLabels()}
        {...paging}
        pageSizeOptions={PAGE_SIZE_OPTIONS}
        paginationAriaLabel={t("searchAnswerProfiles.faq.paginationLabel")}
        columns={[
          {
            key: "question",
            header: t("searchAnswerProfiles.faq.question"),
            rowHeader: true,
            render: (row) => (
              <div className="min-w-0" data-testid={`approved-faq-row-${row.id}`}>
                <span className="break-words">{row.question}</span>
                <FaqScopeSummary row={row} knowledgeBases={knowledgeBases} />
                {/* 要確認・原文の更新の行は、確かめ直す操作を行の中にも出す（375px で操作の列が隠れても届く）。 */}
                {row.review_reasons.length > 0 || row.source_updated ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    icon={ListChecks}
                    className="mt-1.5"
                    onClick={() => openConfirm(row)}
                    data-testid={`approved-faq-confirm-${row.id}`}
                  >
                    {t("searchAnswerProfiles.faq.confirm.action")}
                  </Button>
                ) : null}
              </div>
            ),
          },
          {
            key: "answer",
            header: t("searchAnswerProfiles.faq.answer"),
            render: (row) => (
              <span className="line-clamp-3 whitespace-pre-wrap break-words">
                {row.answer}
              </span>
            ),
          },
          {
            key: "actions",
            header: t("searchAnswerProfiles.faq.actions"),
            align: "right",
            render: (row) => (
              <RowActionMenu
                actions={[
                  {
                    id: "confirm",
                    label: t("searchAnswerProfiles.faq.confirm.action"),
                    icon: ListChecks,
                    onSelect: () => openConfirm(row),
                  },
                  {
                    id: "delete",
                    label: t("searchAnswerProfiles.faq.delete"),
                    icon: Trash2,
                    tone: "danger",
                    loading: remove.isPending && remove.variables?.includes(row.id),
                    onSelect: () => handleDelete(row),
                  },
                ]}
                ariaLabel={t("common.objectActions.aria", { name: row.question })}
                loading={remove.isPending && remove.variables?.includes(row.id)}
                testId={`approved-faq-row-actions-${row.id}`}
              />
            ),
          },
        ]}
        rows={records}
        getRowKey={(row) => row.id}
        dense
        empty={
          <EmptyState
            title={t("searchAnswerProfiles.faq.empty")}
            hint={t("searchAnswerProfiles.faq.emptyHint")}
          />
        }
        ariaLabel={t("searchAnswerProfiles.faq.listAria")}
        scrollAriaLabel={t("searchAnswerProfiles.faq.scrollLabel")}
        scrollTestId="approved-faq-scroll-region"
        paginationTestId="approved-faq-pagination"
      />
      )}

      <div className="grid min-w-0 grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="text-sm font-semibold text-fg">
            {t("searchAnswerProfiles.faq.addTitle")}
          </h4>
          {/* 質問・回答は文章（全幅。#1891）。 */}
          <FormGrid columns={1}>
          <TextField
            id="approved-faq-question"
            width="full"
            label={t("searchAnswerProfiles.faq.question")}
            value={question}
            onChange={(event) => {
              setQuestion(event.target.value);
              setAddErrors((current) => ({ ...current, question: null }));
            }}
            maxLength={1000}
            error={addErrors.question ?? undefined}
            required
          />
          <TextareaField
            id="approved-faq-answer"
            label={t("searchAnswerProfiles.faq.answer")}
            required
            error={addErrors.answer ?? undefined}
            value={answer}
            onChange={(event) => {
              setAnswer(event.target.value);
              setAddErrors((current) => ({ ...current, answer: null }));
            }}
            rows={4}
            maxLength={20000}
            width="full"
          />
          <SearchableMultiSelect
            id="approved-faq-targets"
            width="lg"
            label={t("searchAnswerProfiles.faq.targets.label")}
            helper={t("searchAnswerProfiles.faq.targets.help")}
            required
            error={addErrors.targets ?? undefined}
            options={knowledgeBaseOptions}
            value={effectiveAddTargets}
            disabled={!query.data}
            labels={knowledgeBaseMultiSelectLabels()}
            onValueChange={(value) => {
              setAddTargets(value);
              setAddErrors((current) => ({ ...current, targets: null }));
            }}
          />
          </FormGrid>
          {/* 追加のボタンは押せる状態のまま、未入力は押したときに欄の直下へ出す（#541）。文言は backend と同じ。 */}
          <Button
            size="sm"
            icon={Plus}
            loading={add.isPending}
            onClick={() => {
              const nextErrors = {
                question: requiredTextError(question, t("searchAnswerProfiles.faq.error.questionRequired")),
                answer: requiredTextError(answer, t("searchAnswerProfiles.faq.error.answerRequired")),
                targets: effectiveAddTargets.length ? null : targetsRequired,
              };
              setAddErrors(nextErrors);
              if (
                focusFirstInvalidField([
                  ["approved-faq-question", nextErrors.question],
                  ["approved-faq-answer", nextErrors.answer],
                  ["approved-faq-targets", nextErrors.targets],
                ])
              ) {
                return;
              }
              add.mutate(
                {
                  question: question.trim(),
                  answer: answer.trim(),
                  target_knowledge_base_ids: effectiveAddTargets,
                },
                {
                  onSuccess: () => {
                    setQuestion("");
                    setAnswer("");
                    setAddTargets(null);
                    toast.success(t("searchAnswerProfiles.faq.added"));
                  },
                  onError: (error) =>
                    toast.error(
                      errorMessage(error, t("searchAnswerProfiles.faq.saveError")),
                    ),
                },
              );
            }}
          >
            {t("searchAnswerProfiles.faq.add")}
          </Button>
        </section>

        <section className="min-w-0 space-y-3 rounded-md border border-border p-3">
          <h4 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
            <FileSpreadsheet size={16} aria-hidden />
            {t("searchAnswerProfiles.faq.importTitle")}
          </h4>
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("searchAnswerProfiles.faq.importHelp")}
          </p>
          {/* 取込はファイルを選ぶまで実行できない（取込モードは既定値があるので必須にしない） */}
          <FieldLabel
            htmlFor="approved-faq-import-file"
            label={t("searchAnswerProfiles.faq.importFile")}
            required
            className="block"
          />
          <input
            id="approved-faq-import-file"
            type="file"
            accept=".xlsx,.xls"
            required
            onChange={(event) =>
              void selectFile(event.target.files?.[0] ?? null)
            }
            className="block w-full text-sm text-fg file:mr-3 file:rounded-md file:border file:border-border-control file:bg-surface file:px-3 file:py-1.5 file:text-sm"
          />
          <SelectField
            id="approved-faq-import-mode"
            label={t("searchAnswerProfiles.faq.importModeLabel")}
            value={mode}
            options={IMPORT_MODE_OPTIONS}
            onValueChange={(value) => value && setMode(value)}
            width="md"
          />
          <SearchableMultiSelect
            id="approved-faq-import-targets"
            width="lg"
            label={t("searchAnswerProfiles.faq.targets.label")}
            helper={t("searchAnswerProfiles.faq.targets.importHelp")}
            required
            error={importTargetsError ?? undefined}
            options={knowledgeBaseOptions}
            value={effectiveImportTargets}
            disabled={!query.data}
            labels={knowledgeBaseMultiSelectLabels()}
            onValueChange={(value) => {
              setImportTargets(value);
              setImportTargetsError(null);
            }}
          />
          {previewError ? (
            <FormStatus tone="danger" message={previewError} />
          ) : null}
          {preview ? (
            <div className="space-y-1 text-xs text-fg-muted">
              <p>
                {t("searchAnswerProfiles.faq.previewCount", { count: preview.total })}
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
            onClick={() => {
              if (!file) return;
              if (!effectiveImportTargets.length) {
                setImportTargetsError(targetsRequired);
                focusFirstInvalidField([["approved-faq-import-targets", targetsRequired]]);
                return;
              }
              importFaq.mutate(
                { file, mode, targets: effectiveImportTargets },
                {
                  onSuccess: (data) =>
                    toast.success(
                      t("searchAnswerProfiles.faq.imported", {
                        inserted: data.inserted_count,
                        deleted: data.deleted_count,
                      }),
                    ),
                  onError: (error) =>
                    toast.error(
                      errorMessage(error, t("searchAnswerProfiles.faq.saveError")),
                    ),
                },
              );
            }}
          >
            {t("searchAnswerProfiles.faq.import")}
          </Button>
          {importFaq.isPending ? (
            <ProcessingIndicator
              active
              label={t("searchAnswerProfiles.faq.importing")}
              operationKey="approved-faq-import"
              placement="action"
              activityIcon="none"
              testId="approved-faq-import-processing"
            />
          ) : null}
        </section>
      </div>

      {/* 対象の KB と根拠を確かめ直す（要確認からの復帰・対象の変更。#4621）。 */}
      <SideSheet
        open={confirming !== null}
        onClose={() => setConfirming(null)}
        title={t("searchAnswerProfiles.faq.confirm.title")}
        closeLabel={t("searchAnswerProfiles.faq.confirm.close")}
        side="right"
        size="md"
        id="approved-faq-confirm-sheet"
      >
        {confirming ? (
          <div className="space-y-4" data-testid="approved-faq-confirm">
            <p className="text-sm leading-relaxed text-fg-muted">
              {t("searchAnswerProfiles.faq.confirm.description")}
            </p>
            <div className="min-w-0 rounded-md border border-border p-3">
              <p className="break-words text-sm font-medium text-fg">{confirming.question}</p>
              <FaqScopeSummary row={confirming} knowledgeBases={knowledgeBases} />
            </div>
            <SearchableMultiSelect
              id="approved-faq-confirm-targets"
              width="lg"
              label={t("searchAnswerProfiles.faq.targets.label")}
              helper={t("searchAnswerProfiles.faq.targets.help")}
              required
              error={confirmError ?? undefined}
              options={knowledgeBaseOptions}
              value={confirmTargets}
              labels={knowledgeBaseMultiSelectLabels()}
              onValueChange={(value) => {
                setConfirmTargets(value);
                setConfirmError(null);
              }}
            />
            <div className="flex flex-wrap gap-2">
              <Button icon={CheckCircle2} loading={confirmScope.isPending} onClick={submitConfirm}>
                {t("searchAnswerProfiles.faq.confirm.submit")}
              </Button>
              <Button variant="secondary" onClick={() => setConfirming(null)}>
                {t("searchAnswerProfiles.faq.confirm.cancel")}
              </Button>
            </div>
          </div>
        ) : null}
      </SideSheet>
    </div>
  );
}
