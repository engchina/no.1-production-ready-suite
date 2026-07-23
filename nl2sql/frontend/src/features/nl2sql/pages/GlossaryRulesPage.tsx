import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Download,
  RefreshCw,
} from "lucide-react";

import {
  Button,
  EmptyState,
  PageHeader,
  Pagination,
  StatusBadge,
  toast,
  usePagination,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiFetch, apiGet, isAbortError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  FileInputControl,
  downloadBlob,
} from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import type { LegacyLearningMaterialData } from "../types";

const GLOSSARY_RULES_ID = "glossary-rules";
const GLOBAL_PAGE_SIZE = 10;
const GLOBAL_PREVIEW_TEXT_CLASS =
  "max-h-[15rem] min-w-0 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] pr-2 leading-6";

export function GlossaryRulesPage() {
  const [legacyMaterial, setLegacyMaterial] = useState<LegacyLearningMaterialData>({
    glossary: {},
    rules: [],
  });
  const [loading, setLoading] = useState(false);
  const [legacyBusy, setLegacyBusy] = useState(false);
  // danger（原因+対処）のみ Banner で常設表示。成功の「瞬間」は toast で 1 回通知する（messaging-spec §9 P1）。
  const [errorText, setErrorText] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState("");
  const [legacyTermsFilename, setLegacyTermsFilename] = useState("");
  const loadSequence = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const initialLoadStartedRef = useRef(false);
  const cleanupTimerRef = useRef<number | null>(null);

  const legacyTerms = useMemo(
    () =>
      Object.entries(legacyMaterial.glossary).map(([term, definition]) => ({
        term,
        definition,
      })),
    [legacyMaterial.glossary]
  );

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setErrorText(null);
    const controller = new AbortController();
    loadControllerRef.current = controller;
    try {
      const legacyData = await apiGet<LegacyLearningMaterialData>(
        "/api/nl2sql/legacy-learning-material",
        { signal: controller.signal }
      );
      if (controller.signal.aborted || sequence !== loadSequence.current) return;
      setLegacyMaterial(legacyData);
      setLastLoadedAt(new Date().toISOString());
      if (announce) {
        toast.success(t("glossary.message.serverLoaded"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setErrorText(err instanceof Error ? err.message : t("glossary.error.load"));
    } finally {
      if (loadControllerRef.current === controller) loadControllerRef.current = null;
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (cleanupTimerRef.current !== null) {
      window.clearTimeout(cleanupTimerRef.current);
      cleanupTimerRef.current = null;
    }
    if (!initialLoadStartedRef.current) {
      initialLoadStartedRef.current = true;
      void load();
    }
    return () => {
      cleanupTimerRef.current = window.setTimeout(() => {
        cleanupTimerRef.current = null;
        loadSequence.current += 1;
        loadControllerRef.current?.abort();
      }, 0);
    };
  }, []);

  const importLegacyTerms = async (file: File) => {
    setLegacyTermsFilename(file.name);
    setLegacyBusy(true);
    setErrorText(null);
    try {
      const data = await uploadLegacyLearningMaterialFile(file);
      setLegacyMaterial(data);
      toast.success(t("glossary.message.legacyImported", { terms: Object.keys(data.glossary).length }));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : t("glossary.error.importMaterial"));
    } finally {
      setLegacyBusy(false);
    }
  };

  const exportLegacyTerms = async () => {
    setLegacyBusy(true);
    setErrorText(null);
    try {
      const filename = "terms.xlsx";
      const response = await apiFetch("/api/nl2sql/legacy-learning-material/terms/export.xlsx", {
        headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
      });
      if (!response.ok) throw new Error(t("glossary.error.exportMaterial"));
      downloadBlob(filename, await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : t("glossary.error.exportMaterial"));
    } finally {
      setLegacyBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={t("nav.glossaryRules")}
        subtitle={t("glossary.subtitle")}
      />
      <main className="grid gap-4 p-4 lg:p-8">
        <GlossaryStatusBar
          termsCount={legacyTerms.length}
          lastLoadedAt={lastLoadedAt}
          loading={loading}
          onRefresh={() => void load(true)}
        />

        <PageNotice notice={errorText ? { tone: "danger", message: errorText } : null} />

        <DbObjectManagementPanelShell
          id="glossary-rules-panel-globalTerms"
          labelledBy="glossary-rules-panel-heading"
          idPrefix={GLOSSARY_RULES_ID}
          ariaLabel={t("glossary.globalTerms.workspace")}
        >
          <GlobalMaterialPanel
            headingId="glossary-rules-panel-heading"
            title={t("glossary.globalTerms.title")}
            description={t("glossary.globalTerms.hint")}
            countLabel={t("glossary.count.terms", { count: legacyTerms.length })}
            importLabel={t("glossary.globalTerms.import")}
            exportLabel={t("glossary.globalTerms.export")}
            filename={legacyTermsFilename}
            busy={legacyBusy}
            loading={loading && !lastLoadedAt}
            rows={legacyTerms}
            onImport={(file) => void importLegacyTerms(file)}
            onExport={() => void exportLegacyTerms()}
          />
        </DbObjectManagementPanelShell>
      </main>
    </>
  );
}

async function uploadLegacyLearningMaterialFile(
  file: File
): Promise<LegacyLearningMaterialData> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiFetch("/api/nl2sql/legacy-learning-material/terms/import", {
    method: "POST",
    body: form,
    headers: { Accept: "application/json" },
  });
  const payload = (await response.json()) as {
    data?: LegacyLearningMaterialData;
    error?: string;
    detail?: string;
  };
  if (!response.ok || !payload.data) {
    throw new Error(payload.error || payload.detail || t("glossary.error.importMaterial"));
  }
  return payload.data;
}

function GlossaryStatusBar({
  termsCount,
  lastLoadedAt,
  loading,
  onRefresh,
}: {
  termsCount: number;
  lastLoadedAt: string;
  loading: boolean;
  onRefresh: () => void;
}) {
  return (
    <DbObjectManagementStatusBar
      ariaLabel={t("glossary.status.label")}
      metricColumnsClass="sm:grid-cols-2 xl:grid-cols-3"
      metrics={[
        { label: t("glossary.status.terms"), value: formatNumber(termsCount), emphasis: true },
        { label: t("glossary.status.lastLoaded"), value: formatDateTime(lastLoadedAt) },
      ]}
      actions={
        <Button type="button" variant="secondary" size="sm" loading={loading} onClick={onRefresh}>
          <RefreshCw size={15} aria-hidden="true" />
          <span>{t("glossary.action.refresh")}</span>
        </Button>
      }
    />
  );
}

function GlobalMaterialPanel({
  headingId,
  title,
  description,
  countLabel,
  importLabel,
  exportLabel,
  filename,
  busy,
  loading,
  rows,
  onImport,
  onExport,
}: {
  headingId: string;
  title: string;
  description: string;
  countLabel: string;
  importLabel: string;
  exportLabel: string;
  filename: string;
  busy: boolean;
  loading: boolean;
  rows: Array<{ term: string; definition: string }>;
  onImport: (file: File) => void;
  onExport: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-3">
      <DbObjectPanelHeader
        headingId={headingId}
        title={title}
        description={description}
        icon={BookOpen}
        action={<StatusBadge variant="neutral" label={countLabel} />}
      />
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
        <FileInputControl
          label={importLabel}
          ariaLabel={importLabel}
          accept=".xlsx,.xlsm,.csv,.tsv,.txt"
          filename={filename}
          selectedText={filename}
          emptyText={t("glossary.file.emptyWorkbook")}
          pickText={t("glossary.file.pickWorkbook")}
          replaceText={t("glossary.file.replaceWorkbook")}
          icon="spreadsheet"
          disabled={busy}
          onPick={onImport}
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="min-h-11 md:self-end"
          loading={busy}
          onClick={onExport}
        >
          <Download size={15} aria-hidden="true" />
          <span>{exportLabel}</span>
        </Button>
      </div>
      {loading ? (
        <DbManagementLoadingSkeleton
          idPrefix="glossary-terms"
          ariaLabel={t("glossary.globalTerms.loading")}
          variant="list"
          rows={6}
        />
      ) : (
        <GlobalPreviewTable rows={rows} />
      )}
    </section>
  );
}

function GlobalPreviewTable({
  rows,
}: {
  rows: Array<{ term: string; definition: string }>;
}) {
  const { page: currentPage, setPage, totalPages, pageItems: visibleRows, range } = usePagination(
    rows,
    GLOBAL_PAGE_SIZE
  );
  const start = range.start === 0 ? 0 : range.start - 1;

  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <EmptyState title={t("glossary.legacy.empty")} hint={t("glossary.legacy.emptyHint")} />
      </div>
    );
  }

  return (
    <div className="grid gap-2" data-testid="glossary-terms-preview">
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-border text-left text-sm">
            <colgroup>
              <col className="w-12" />
              <col className="w-32 sm:w-56" />
              <col />
            </colgroup>
            <thead className="bg-background text-xs font-semibold uppercase text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-right">
                  {t("glossary.preview.rowNumber")}
                </th>
                <th scope="col" className="px-3 py-2">
                  TERM
                </th>
                <th scope="col" className="px-3 py-2">
                  DEFINITION
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70 text-foreground">
              {visibleRows.map((row, index) => {
                const absoluteIndex = start + index;
                return (
                  <tr key={`${row.term}-${absoluteIndex}`}>
                    <td
                      className="px-3 py-2 text-right text-xs tabular-nums text-muted"
                      data-testid="glossary-terms-row-number"
                    >
                      {absoluteIndex + 1}
                    </td>
                    <td
                      className="px-3 py-2 align-middle font-mono text-xs text-foreground [overflow-wrap:anywhere]"
                      data-testid="glossary-term-preview-cell"
                    >
                      {row.term}
                    </td>
                    <td className="min-w-0 px-3 py-2 align-top">
                      <div className={GLOBAL_PREVIEW_TEXT_CLASS} data-testid="glossary-definition-preview-text">
                        {row.definition}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
      <Pagination
        page={currentPage}
        totalPages={totalPages}
        onPageChange={setPage}
        summary={t("glossary.pagination.range", { start: range.start, end: range.end, total: range.total })}
        pageIndicator={t("glossary.pagination.page", { page: currentPage, total: totalPages })}
        prevLabel={t("glossary.pagination.prev")}
        nextLabel={t("glossary.pagination.next")}
        ariaLabel={t("glossary.pagination.label")}
        testId="glossary-terms-pagination"
      />
    </div>
  );
}
