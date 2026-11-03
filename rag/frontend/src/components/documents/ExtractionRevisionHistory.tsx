import { ArrowLeftRight, History, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  Banner,
  Button,
  DEFAULT_PAGE_SIZE,
  type DataTableColumn,
  EmptyState,
  ListToolbar,
  OffsetPagination,
  PAGE_SIZE_OPTIONS,
  PagedDataTable,
  RowActionMenu,
  SelectField,
  SideSheet,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
  offsetForPage,
  resolveContentLang,
} from "@production-ready/ui";

import { ApiErrorState } from "@/components/StateViews";
import { useConfirm } from "@/components/ui/confirm-dialog";
import type {
  ExtractionRevisionElementChange,
  ExtractionRevisionHistory as ExtractionRevisionHistoryData,
  ExtractionRevisionSummary,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import {
  useDocumentRecipeExtractionRevisionDiff,
  useRestoreDocumentRecipeExtractionRevision,
} from "@/lib/queries";
import { toast } from "@/lib/toast";

const KIND_LABEL: Record<ExtractionRevisionSummary["kind"], I18nKey> = {
  parsed: "flow.review.revisions.kind.parsed",
  edited: "flow.review.revisions.kind.edited",
  restored: "flow.review.revisions.kind.restored",
};

const CHANGE_LABEL: Record<ExtractionRevisionElementChange["change"], I18nKey> = {
  added: "flow.review.revisions.change.added",
  removed: "flow.review.revisions.change.removed",
  changed: "flow.review.revisions.change.changed",
};

function revisionLabel(revisionNo: number) {
  return t("flow.review.revisions.label", { revision: revisionNo });
}

/**
 * 抽出の修正の履歴（#2644）。版の一覧・版の差分・旧版の復元を side sheet で扱う。
 *
 * 復元は新しい版を作る（過去の版を書き換えない）。編集中の未保存の修正があるときは、復元で失わないよう
 * 止める。解析をやり直した後は、修正の前提が変わったことを示すだけで自動では書き換えない。
 */
export function ExtractionRevisionHistory({
  documentId,
  recipeId,
  open,
  onClose,
  history,
  loading,
  error,
  onRetry,
  canRestore,
  hasUnsavedEdits,
  currentExtractionRecipeId,
}: {
  documentId: string;
  recipeId: string;
  open: boolean;
  onClose: () => void;
  history: ExtractionRevisionHistoryData | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  /** レシピが確認待ち（修正できる状態）。 */
  canRestore: boolean;
  hasUnsavedEdits: boolean;
  currentExtractionRecipeId: string | null;
}) {
  const confirm = useConfirm();
  const restore = useRestoreDocumentRecipeExtractionRevision();
  const [viewedRevision, setViewedRevision] = useState<number | null>(null);
  const paging = useClientListPaging("documents.extractionRevisionsPaging", `${documentId}:${recipeId}`);
  const revisions = history?.revisions ?? [];
  const viewed = revisions.find((row) => row.revision_no === viewedRevision) ?? null;

  const runRestore = async (row: ExtractionRevisionSummary) => {
    if (!currentExtractionRecipeId) return;
    const ok = await confirm({
      title: t("flow.review.revisions.restoreTitle", { revision: row.revision_no }),
      description: t(
        row.base_changed
          ? "flow.review.revisions.restoreDescriptionBaseChanged"
          : "flow.review.revisions.restoreDescription",
      ),
      confirmLabel: t("flow.review.revisions.restore"),
      tone: "warning",
    });
    if (!ok) return;
    restore.mutate(
      {
        id: documentId,
        recipeId,
        revisionNo: row.revision_no,
        expectedExtractionRecipeId: currentExtractionRecipeId,
      },
      {
        onSuccess: () => {
          toast.success(t("flow.review.revisions.restored", { revision: row.revision_no }));
          setViewedRevision(null);
        },
      },
    );
  };

  const columns: DataTableColumn<ExtractionRevisionSummary>[] = [
    {
      key: "revision",
      header: t("flow.review.revisions.col.revision"),
      rowHeader: true,
      // 375px でも読めるよう、種類と日時は版の下に置く（列を増やさない）。
      render: (row) => (
        <span className="block space-y-1">
          <span className="inline-flex flex-wrap items-center gap-2">
            <span className="tnum font-medium text-fg">{revisionLabel(row.revision_no)}</span>
            {row.is_current ? (
              <StatusBadge variant="success" label={t("flow.review.revisions.current")} />
            ) : null}
            {row.is_serving ? (
              <StatusBadge variant="info" label={t("flow.review.revisions.serving")} />
            ) : null}
          </span>
          <span className="block text-xs font-normal text-fg-muted">
            {t(KIND_LABEL[row.kind])}
            {" ・ "}
            <span className="tnum">{formatDateTime(row.created_at)}</span>
          </span>
        </span>
      ),
    },
    {
      key: "note",
      header: t("flow.review.revisions.col.note"),
      render: (row) => <RevisionNote row={row} />,
    },
    {
      key: "actions",
      header: t("flow.review.revisions.col.actions"),
      render: (row) => (
        <RowActionMenu
          actions={[
            {
              id: "diff",
              label: t("flow.review.revisions.viewDiff"),
              icon: ArrowLeftRight,
              onSelect: () => setViewedRevision(row.revision_no),
            },
            {
              id: "restore",
              label: t("flow.review.revisions.restore"),
              icon: RotateCcw,
              visible: canRestore && !row.is_current && !row.settings_changed,
              disabled: hasUnsavedEdits || restore.isPending,
              loading: restore.isPending && restore.variables?.revisionNo === row.revision_no,
              onSelect: () => void runRestore(row),
            },
          ]}
          ariaLabel={t("common.objectActions.aria", { name: revisionLabel(row.revision_no) })}
          loading={restore.isPending && restore.variables?.revisionNo === row.revision_no}
          testId={`extraction-revision-actions-${row.revision_no}`}
        />
      ),
    },
  ];

  return (
    <SideSheet
      open={open}
      onClose={onClose}
      side="right"
      size="wide"
      initialFocus="dialog"
      title={t("flow.review.revisions.title")}
      closeLabel={t("common.dismiss")}
      id="extraction-revision-history"
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto" data-testid="extraction-revision-history">
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("flow.review.revisions.hint")}
          {history ? t("flow.review.revisions.retention", { count: history.max_revisions }) : null}
          {canRestore ? null : t("flow.review.revisions.restoreOnlyInReview")}
        </p>
        {history?.reparsed_since_last_revision ? <ReparsedNotice /> : null}
        {canRestore && hasUnsavedEdits ? (
          <Banner severity="warning" title={t("flow.review.revisions.unsavedBlocked")} />
        ) : null}
        {restore.isError ? (
          <ApiErrorState error={restore.error} fallback={t("flow.review.revisions.restoreError")} />
        ) : null}
        {loading && !history ? (
          <div className="space-y-2">
            <TimedLoadingState
              label={t("flow.review.revisions.loading")}
              operationKey={`extraction-revisions:${recipeId}`}
            />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : null}
        {error && !history ? (
          <ApiErrorState
            error={error}
            fallback={t("flow.review.revisions.loadError")}
            onRetry={onRetry}
          />
        ) : null}
        {history ? (
          <>
            <ListToolbar
              count={listCountProps({ matchedTotal: revisions.length, filtered: false, live: true })}
              testId="extraction-revision-toolbar"
            />
            <PagedDataTable<ExtractionRevisionSummary>
              paginationLabels={paginationLabels()}
              {...paging}
              pageSizeOptions={PAGE_SIZE_OPTIONS}
              paginationAriaLabel={t("flow.review.revisions.paginationLabel")}
              columns={columns}
              rows={revisions}
              getRowKey={(row) => String(row.revision_no)}
              selectedRowKey={viewedRevision == null ? null : String(viewedRevision)}
              dense
              empty={<EmptyState title={t("flow.review.revisions.empty")} />}
              ariaLabel={t("flow.review.revisions.title")}
              scrollAriaLabel={t("flow.review.revisions.scrollLabel")}
              scrollTestId="extraction-revision-scroll-region"
            />
          </>
        ) : null}
        {viewed ? (
          <RevisionDiff
            key={viewed.revision_no}
            documentId={documentId}
            recipeId={recipeId}
            revision={viewed}
            revisions={revisions}
          />
        ) : null}
      </div>
    </SideSheet>
  );
}

/** 解析をやり直した後で、修正の前提が変わったことの案内（自動では書き換えない）。 */
export function ReparsedNotice() {
  return (
    <Banner severity="warning" title={t("flow.review.revisions.reparsedTitle")}>
      {t("flow.review.revisions.reparsedDescription")}
    </Banner>
  );
}

function RevisionNote({ row }: { row: ExtractionRevisionSummary }) {
  const notes: string[] = [];
  if (row.restored_from_revision_no != null) {
    notes.push(t("flow.review.revisions.restoredFrom", { revision: row.restored_from_revision_no }));
  }
  if (row.kind !== "parsed") {
    notes.push(t("flow.review.revisions.changedElements", { count: row.changed_element_count }));
  }
  if (row.settings_changed) {
    notes.push(t("flow.review.revisions.settingsChanged"));
  } else if (row.base_changed) {
    notes.push(t("flow.review.revisions.baseChanged"));
  }
  return (
    <span className="block min-w-[9rem] space-y-0.5 text-xs">
      {notes.map((note) => (
        <span key={note} className="block break-words">
          {note}
        </span>
      ))}
    </span>
  );
}

function RevisionDiff({
  documentId,
  recipeId,
  revision,
  revisions,
}: {
  documentId: string;
  recipeId: string;
  revision: ExtractionRevisionSummary;
  revisions: ExtractionRevisionSummary[];
}) {
  // 比べる版の既定は親の版（API に任せる）。選ぶと、その版と比べる。
  const [against, setAgainst] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const diff = useDocumentRecipeExtractionRevisionDiff(documentId, recipeId, revision.revision_no, {
    against,
    offset: offsetForPage(page, pageSize),
    limit: pageSize,
  });
  const data = diff.data;
  const selected = against ?? data?.against_revision_no ?? null;
  // 狭い画面では一覧の下で見えないので、開いたら差分へ移す（版を変えると作り直される）。
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => {
    sectionRef.current?.scrollIntoView({ block: "start" });
  }, []);
  return (
    <section
      ref={sectionRef}
      className="min-w-0 space-y-3 border-t border-border pt-4"
      aria-labelledby="extraction-revision-diff"
      data-testid="extraction-revision-diff"
    >
      <h3 id="extraction-revision-diff" className="text-sm font-semibold text-fg">
        {t("flow.review.revisions.diffTitle", { revision: revision.revision_no })}
      </h3>
      <SelectField
        id="extraction-revision-against"
        label={t("flow.review.revisions.against")}
        width="md"
        value={selected == null ? "" : String(selected)}
        placeholder={t("flow.review.revisions.againstPlaceholder")}
        options={revisions
          .filter((row) => row.revision_no !== revision.revision_no)
          .map((row) => ({
            value: String(row.revision_no),
            label: `${revisionLabel(row.revision_no)}（${t(KIND_LABEL[row.kind])}）`,
          }))}
        onValueChange={(value) => {
          setAgainst(Number(value));
          setPage(1);
        }}
      />
      {diff.isPending ? (
        <div className="space-y-2">
          <TimedLoadingState
            label={t("flow.review.revisions.diffLoading")}
            operationKey={`extraction-revision-diff:${recipeId}:${revision.revision_no}`}
          />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}
      {diff.isError ? (
        <ApiErrorState
          error={diff.error}
          fallback={t("flow.review.revisions.diffError")}
          onRetry={() => void diff.refetch()}
        />
      ) : null}
      {data && data.against_revision_no == null ? (
        <EmptyState title={t("flow.review.revisions.noBaseline")} />
      ) : null}
      {data && data.against_revision_no != null && data.total === 0 ? (
        <EmptyState title={t("flow.review.revisions.noChanges")} />
      ) : null}
      {data && data.total > 0 ? (
        <>
          <ul className="space-y-3">
            {data.changes.map((change) => (
              <RevisionChange key={change.element_id} change={change} />
            ))}
          </ul>
          <OffsetPagination
            offset={data.offset}
            limit={data.limit}
            total={data.total}
            count={data.changes.length}
            onPageChange={setPage}
            pageSizeOptions={PAGE_SIZE_OPTIONS}
            onPageSizeChange={(size) => {
              setPageSize(size);
              setPage(1);
            }}
            labels={paginationLabels()}
            ariaLabel={t("flow.review.revisions.diffPaginationLabel")}
            testId="extraction-revision-diff-pagination"
          />
        </>
      ) : null}
    </section>
  );
}

function RevisionChange({ change }: { change: ExtractionRevisionElementChange }) {
  return (
    <li className="min-w-0 space-y-2 rounded-md border border-border bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted">
        <StatusBadge
          variant={change.change === "removed" ? "warning" : "info"}
          label={t(CHANGE_LABEL[change.change])}
        />
        {change.page_number != null ? (
          <span className="tnum">{t("flow.review.revisions.page", { page: change.page_number })}</span>
        ) : null}
        <span className="break-all">{change.element_id}</span>
      </div>
      <div className="grid min-w-0 gap-2 md:grid-cols-2">
        <RevisionText label={t("flow.review.revisions.before")} text={change.before} />
        <RevisionText label={t("flow.review.revisions.after")} text={change.after} />
      </div>
      {change.truncated ? (
        <p className="text-xs text-fg-muted">{t("flow.review.revisions.truncated")}</p>
      ) : null}
    </li>
  );
}

function RevisionText({ label, text }: { label: string; text: string | null }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      {text == null ? (
        <p className="text-sm text-fg-muted">{t("flow.review.revisions.noText")}</p>
      ) : (
        <p lang={resolveContentLang(undefined, text)} className="whitespace-pre-wrap break-words text-sm text-fg">
          {text}
        </p>
      )}
    </div>
  );
}

/** 履歴を開くボタン。確認待ちの間と、版があるレシピで出す。 */
export function ExtractionRevisionHistoryButton({
  onOpen,
  count,
}: {
  onOpen: () => void;
  count: number;
}) {
  return (
    <Button
      size="sm"
      variant="ghost"
      icon={History}
      onClick={onOpen}
      aria-haspopup="dialog"
      aria-controls="extraction-revision-history"
    >
      {count > 0
        ? t("flow.review.revisions.openWithCount", { count })
        : t("flow.review.revisions.open")}
    </Button>
  );
}
