import { Download, FileJson, Plus } from "lucide-react";
import { useState } from "react";

import {
  Button,
  EmptyState,
  ListToolbar,
  RowTitleButton,
  StatusBadge,
  TableSkeleton,
  TimedLoadingState,
  ToggleChip,
  toast,
  PagedDataTable,
} from "@engchina/production-ready-ui";

import { paginationLabels } from "@/lib/pagination-labels";
import { ErrorState } from "@/components/StateViews";
import { api, ApiError, type SupportGuideSummary } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { confirmPendingLeave } from "@/lib/leave-guard";
import { initialLoadError, useSupportGuides } from "@/lib/queries";
import {
  SUPPORT_GUIDE_STATE_VARIANT,
  supportGuideExportFileName,
  supportGuideState,
} from "@/lib/support-guide-form";

import { SupportGuideEditor } from "./SupportGuideEditor";
import { SupportGuideImportPanel } from "./SupportGuideImportPanel";

type Selection = { mode: "none" } | { mode: "new" } | { mode: "edit"; guideId: string };

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** JSON を利用者の端末へ保存させる。 */
function downloadJson(fileName: string, data: unknown) {
  const blob = new Blob([`${JSON.stringify(data, null, 2)}\n`], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * 検索・回答プロファイルの業務ガイド（#1237）。一覧・取り込み・書き出しと、選んだガイドの編集。
 * 編集の状態はフォームが持ち、別のガイドへ移る前に未保存の変更を確かめる。
 */
export function SupportGuideManager({ searchAnswerProfileId }: { searchAnswerProfileId: string }) {
  const [includeArchived, setIncludeArchived] = useState(false);
  const [selection, setSelection] = useState<Selection>({ mode: "none" });
  const [importOpen, setImportOpen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const query = useSupportGuides(searchAnswerProfileId, includeArchived);
  const loadError = initialLoadError(query);
  const selectedId = selection.mode === "edit" ? selection.guideId : null;

  const select = async (next: Selection) => {
    if (!(await confirmPendingLeave())) return;
    setSelection(next);
  };

  const runExport = async () => {
    setExporting(true);
    try {
      const data = await api.exportSupportGuides(searchAnswerProfileId);
      downloadJson(supportGuideExportFileName(searchAnswerProfileId, new Date()), data);
      toast.success(t("supportGuides.exported", { count: data.guides.length }));
    } catch (error) {
      toast.error(errorMessage(error, t("supportGuides.exportError")));
    } finally {
      setExporting(false);
    }
  };

  const rows = query.data ?? [];
  const table = query.isPending ? (
    <TimedLoadingState
      label={t("supportGuides.loading")}
      operationKey={`support-guides-${searchAnswerProfileId}`}
      testId="support-guides-loading"
    >
      <TableSkeleton columns={4} rows={{ base: 3, md: 5 }} />
    </TimedLoadingState>
  ) : loadError ? (
    <ErrorState
      message={errorMessage(loadError, t("supportGuides.loadError"))}
      onRetry={() => void query.refetch()}
    />
  ) : (
    <PagedDataTable<SupportGuideSummary>
      paginationLabels={paginationLabels()}
      columns={[
        {
          key: "title",
          header: t("supportGuides.column.title"),
          rowHeader: true,
          render: (row) => (
            <RowTitleButton
              title={row.title}
              current={selectedId === row.guide_id}
              aria-label={t("supportGuides.editNamed", { name: row.title })}
              onClick={() => void select({ mode: "edit", guideId: row.guide_id })}
            />
          ),
        },
        {
          key: "status",
          header: t("supportGuides.column.status"),
          render: (row) => {
            const state = supportGuideState(row);
            return (
              <StatusBadge
                variant={SUPPORT_GUIDE_STATE_VARIANT[state]}
                label={t(`supportGuides.state.${state}` as I18nKey)}
              />
            );
          },
        },
        {
          key: "published",
          header: t("supportGuides.column.published"),
          render: (row) => (
            <span className="tnum text-sm">
              {row.published_revision != null
                ? t("supportGuides.revisionLabel", { revision: row.published_revision })
                : t("supportGuides.noRevision")}
            </span>
          ),
        },
        {
          key: "updated",
          header: t("supportGuides.column.updated"),
          render: (row) => (
            <span className="tnum text-xs text-fg-muted">
              {row.updated_by
                ? t("supportGuides.updatedBy", { at: formatDateTime(row.updated_at), by: row.updated_by })
                : formatDateTime(row.updated_at)}
            </span>
          ),
        },
      ]}
      rows={rows}
      getRowKey={(row) => row.guide_id}
      onRowClick={(row) => void select({ mode: "edit", guideId: row.guide_id })}
      selectedRowKey={selectedId}
      resetKey={`${searchAnswerProfileId}-${includeArchived}`}
      dense
      empty={
        includeArchived ? (
          <EmptyState title={t("supportGuides.emptyArchived")} />
        ) : (
          <EmptyState title={t("supportGuides.empty")} hint={t("supportGuides.emptyHint")} />
        )
      }
      ariaLabel={t("supportGuides.listAria")}
      scrollAriaLabel={t("supportGuides.scrollLabel")}
      scrollTestId="support-guides-scroll-region"
      paginationTestId="support-guides-pagination"
    />
  );

  return (
    <div className="space-y-5">
      <p className="text-xs leading-relaxed text-fg-muted">{t("supportGuides.hint")}</p>
      <ListToolbar
        filters={
          <div role="group" aria-label={t("supportGuides.filterAria")} className="flex flex-wrap gap-1">
            <ToggleChip selected={includeArchived} onClick={() => setIncludeArchived((value) => !value)}>
              {t("supportGuides.includeArchived")}
            </ToggleChip>
          </div>
        }
        actions={
          <>
            <Button
              size="sm"
              variant="secondary"
              icon={Download}
              loading={exporting}
              onClick={() => void runExport()}
            >
              {t("supportGuides.export")}
            </Button>
            <Button
              size="sm"
              variant="secondary"
              icon={FileJson}
              pressed={importOpen}
              onClick={() => setImportOpen((open) => !open)}
            >
              {t("supportGuides.import")}
            </Button>
            <Button size="sm" icon={Plus} onClick={() => void select({ mode: "new" })}>
              {t("supportGuides.new")}
            </Button>
          </>
        }
        testId="support-guides-toolbar"
      />
      {importOpen ? (
        <SupportGuideImportPanel
          searchAnswerProfileId={searchAnswerProfileId}
          onClose={() => setImportOpen(false)}
        />
      ) : null}
      {table}
      {selection.mode !== "none" ? (
        <SupportGuideEditor
          // ガイドを切り替えたら、編集中の入力を持つフォームを作り直す。
          key={selection.mode === "edit" ? selection.guideId : "new"}
          searchAnswerProfileId={searchAnswerProfileId}
          guideId={selectedId}
          onCreated={(guideId) => setSelection({ mode: "edit", guideId })}
          onClose={() => setSelection({ mode: "none" })}
        />
      ) : null}
    </div>
  );
}
