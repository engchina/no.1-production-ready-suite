import { ArrowLeftRight, RotateCcw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  Banner,
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
  DocumentSectionRevisionChange,
  DocumentSectionRevisionSnapshot,
  DocumentSectionRevisionSummary,
} from "@/lib/api";
import { sectionPageLabel } from "@/lib/document-sections";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { listCountProps, useClientListPaging } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import {
  useDocumentSectionRevisionDiff,
  useDocumentSectionRevisions,
  useRestoreDocumentSectionRevision,
} from "@/lib/queries";
import { toast } from "@/lib/toast";

const KIND_LABEL: Record<DocumentSectionRevisionSummary["kind"], I18nKey> = {
  extracted: "sections.revisions.kind.extracted",
  edited: "sections.revisions.kind.edited",
  restored: "sections.revisions.kind.restored",
  reset: "sections.revisions.kind.reset",
};

const CHANGE_LABEL: Record<DocumentSectionRevisionChange["change"], I18nKey> = {
  added: "sections.revisions.change.added",
  removed: "sections.revisions.change.removed",
  changed: "sections.revisions.change.changed",
  moved: "sections.revisions.change.moved",
};

function revisionLabel(revisionNo: number) {
  return t("sections.revisions.label", { revision: revisionNo });
}

/**
 * 人が修正した章節の履歴（#4171）。版の一覧・版の差分・旧版の復元を side sheet で扱う。
 *
 * 復元は新しい版を作る（過去の版を書き換えない）。編集中は開けないので、未保存の修正を失わない。
 */
export function SectionRevisionHistory({
  documentId,
  recipeId,
  currentRevision,
  open,
  onClose,
}: {
  documentId: string;
  recipeId: string | null;
  /** 画面が読んだ今の版（修正が無ければ null）。 */
  currentRevision: number | null;
  open: boolean;
  onClose: () => void;
}) {
  const confirm = useConfirm();
  const query = useDocumentSectionRevisions(documentId, recipeId, open);
  const restore = useRestoreDocumentSectionRevision(documentId);
  const [viewedRevision, setViewedRevision] = useState<number | null>(null);
  const paging = useClientListPaging("documents.sectionRevisionsPaging", documentId);
  const history = query.data;
  const revisions = history?.revisions ?? [];
  const viewed = revisions.find((row) => row.revision_no === viewedRevision) ?? null;

  const runRestore = async (row: DocumentSectionRevisionSummary) => {
    const ok = await confirm({
      title: t("sections.revisions.restoreTitle", { revision: row.revision_no }),
      description: t(
        row.base_changed
          ? "sections.revisions.restoreDescriptionBaseChanged"
          : "sections.revisions.restoreDescription",
      ),
      confirmLabel: t("sections.revisions.restore"),
      tone: "warning",
    });
    if (!ok) return;
    restore.mutate(
      { revisionNo: row.revision_no, expectedRevision: currentRevision, recipeId },
      {
        onSuccess: () => {
          toast.success(t("sections.revisions.restored", { revision: row.revision_no }));
          setViewedRevision(null);
        },
      },
    );
  };

  const columns: DataTableColumn<DocumentSectionRevisionSummary>[] = [
    {
      key: "revision",
      header: t("sections.revisions.col.revision"),
      rowHeader: true,
      // 375px でも読めるよう、種類と日時は版の下に置く（列を増やさない）。
      render: (row) => (
        <span className="block space-y-1">
          <span className="inline-flex flex-wrap items-center gap-2">
            <span className="tnum font-medium text-fg">{revisionLabel(row.revision_no)}</span>
            {row.is_current ? (
              <StatusBadge variant="success" label={t("sections.revisions.current")} />
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
      header: t("sections.revisions.col.note"),
      render: (row) => <RevisionNote row={row} />,
    },
    {
      key: "actions",
      header: t("sections.revisions.col.actions"),
      render: (row) => (
        <RowActionMenu
          actions={[
            {
              id: "diff",
              label: t("sections.revisions.viewDiff"),
              icon: ArrowLeftRight,
              onSelect: () => setViewedRevision(row.revision_no),
            },
            {
              id: "restore",
              label: t("sections.revisions.restore"),
              icon: RotateCcw,
              visible: !row.is_current,
              disabled: restore.isPending,
              loading: restore.isPending && restore.variables?.revisionNo === row.revision_no,
              onSelect: () => void runRestore(row),
            },
          ]}
          ariaLabel={t("common.objectActions.aria", { name: revisionLabel(row.revision_no) })}
          loading={restore.isPending && restore.variables?.revisionNo === row.revision_no}
          testId={`section-revision-actions-${row.revision_no}`}
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
      title={t("sections.revisions.title")}
      closeLabel={t("common.dismiss")}
      id="section-revision-history"
    >
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto" data-testid="section-revision-history">
        <p className="text-xs leading-relaxed text-fg-muted">
          {t("sections.revisions.hint")}
          {history ? t("sections.revisions.retention", { count: history.max_revisions }) : null}
        </p>
        {restore.isError ? (
          <ApiErrorState error={restore.error} fallback={t("sections.revisions.restoreError")} />
        ) : null}
        {query.isFetching && !history ? (
          <div className="space-y-2">
            <TimedLoadingState
              label={t("sections.revisions.loading")}
              operationKey={`section-revisions:${documentId}`}
            />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : null}
        {query.isError && !history ? (
          <ApiErrorState
            error={query.error}
            fallback={t("sections.revisions.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : null}
        {history ? (
          <>
            <ListToolbar
              count={listCountProps({ matchedTotal: revisions.length, filtered: false, live: true })}
              testId="section-revision-toolbar"
            />
            <PagedDataTable<DocumentSectionRevisionSummary>
              paginationLabels={paginationLabels()}
              {...paging}
              pageSizeOptions={PAGE_SIZE_OPTIONS}
              paginationAriaLabel={t("sections.revisions.paginationLabel")}
              columns={columns}
              rows={revisions}
              getRowKey={(row) => String(row.revision_no)}
              selectedRowKey={viewedRevision == null ? null : String(viewedRevision)}
              dense
              empty={<EmptyState title={t("sections.revisions.empty")} />}
              ariaLabel={t("sections.revisions.title")}
              scrollAriaLabel={t("sections.revisions.scrollLabel")}
              scrollTestId="section-revision-scroll-region"
            />
          </>
        ) : null}
        {viewed ? (
          <RevisionDiff
            key={viewed.revision_no}
            documentId={documentId}
            revision={viewed}
            revisions={revisions}
          />
        ) : null}
      </div>
    </SideSheet>
  );
}

/** 人の修正の基にした抽出が今の抽出と違うことの案内（自動では書き換えない）。 */
export function SectionBaseChangedNotice({ outlineChanged }: { outlineChanged: boolean }) {
  return (
    <div data-testid="sections-base-changed">
      <Banner
        severity={outlineChanged ? "warning" : "info"}
        title={t(outlineChanged ? "sections.base.outlineChangedTitle" : "sections.base.changedTitle")}
      >
        {t(outlineChanged ? "sections.base.outlineChangedDescription" : "sections.base.changedDescription")}
      </Banner>
    </div>
  );
}

function RevisionNote({ row }: { row: DocumentSectionRevisionSummary }) {
  const notes: string[] = [];
  if (row.restored_from_revision_no != null) {
    notes.push(t("sections.revisions.restoredFrom", { revision: row.restored_from_revision_no }));
  }
  if (row.kind !== "extracted") {
    notes.push(t("sections.revisions.changedSections", { count: row.changed_section_count }));
  }
  if (row.kind === "edited" || row.kind === "restored") {
    if (row.base_extraction_recipe_id == null) {
      notes.push(t("sections.revisions.baseUnknown"));
    } else if (row.base_outline_changed) {
      notes.push(t("sections.revisions.baseOutlineChanged"));
    } else if (row.base_changed) {
      notes.push(t("sections.revisions.baseChanged"));
    }
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
  revision,
  revisions,
}: {
  documentId: string;
  revision: DocumentSectionRevisionSummary;
  revisions: DocumentSectionRevisionSummary[];
}) {
  // 比べる版の既定は親の版（API に任せる）。選ぶと、その版と比べる。
  const [against, setAgainst] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const diff = useDocumentSectionRevisionDiff(documentId, revision.revision_no, {
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
      aria-labelledby="section-revision-diff"
      data-testid="section-revision-diff"
    >
      <h3 id="section-revision-diff" className="text-sm font-semibold text-fg">
        {t("sections.revisions.diffTitle", { revision: revision.revision_no })}
      </h3>
      <SelectField
        id="section-revision-against"
        label={t("sections.revisions.against")}
        width="md"
        value={selected == null ? "" : String(selected)}
        placeholder={t("sections.revisions.againstPlaceholder")}
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
            label={t("sections.revisions.diffLoading")}
            operationKey={`section-revision-diff:${documentId}:${revision.revision_no}`}
          />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : null}
      {diff.isError ? (
        <ApiErrorState
          error={diff.error}
          fallback={t("sections.revisions.diffError")}
          onRetry={() => void diff.refetch()}
        />
      ) : null}
      {data && data.against_revision_no == null ? (
        <EmptyState title={t("sections.revisions.noBaseline")} />
      ) : null}
      {data && data.against_revision_no != null && data.total === 0 ? (
        <EmptyState title={t("sections.revisions.noChanges")} />
      ) : null}
      {data && data.total > 0 ? (
        <>
          <ul className="space-y-3">
            {data.changes.map((change) => (
              <RevisionChange key={change.section_id} change={change} />
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
            ariaLabel={t("sections.revisions.diffPaginationLabel")}
            testId="section-revision-diff-pagination"
          />
        </>
      ) : null}
    </section>
  );
}

function RevisionChange({ change }: { change: DocumentSectionRevisionChange }) {
  return (
    <li className="min-w-0 space-y-2 rounded-md border border-border bg-surface p-3">
      <StatusBadge
        variant={change.change === "removed" ? "warning" : "info"}
        label={t(CHANGE_LABEL[change.change])}
      />
      <div className="grid min-w-0 gap-2 md:grid-cols-2">
        <RevisionSnapshot label={t("sections.revisions.before")} section={change.before} />
        <RevisionSnapshot label={t("sections.revisions.after")} section={change.after} />
      </div>
    </li>
  );
}

function RevisionSnapshot({
  label,
  section,
}: {
  label: string;
  section: DocumentSectionRevisionSnapshot | null;
}) {
  const pages = section ? sectionPageLabel(section) : null;
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      {section == null ? (
        <p className="text-sm text-fg-muted">{t("sections.revisions.none")}</p>
      ) : (
        <>
          <p lang={resolveContentLang(undefined, section.title)} className="break-words text-sm text-fg">
            {section.title}
          </p>
          <p className="tnum text-xs text-fg-muted">
            {t("sections.revisions.level", { level: section.level })}
            {pages ? ` ・ ${pages}` : null}
          </p>
        </>
      )}
    </div>
  );
}
