import { useEffect, useRef, useState } from "react";
import { Download, Layers3, RefreshCw } from "lucide-react";

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
import { FileInputControl, downloadBlob } from "../components/DbAdminShared";
import {
  DbManagementLoadingSkeleton,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import type { LegacyLearningMaterialData } from "../types";

const GLOBAL_RULES_ID = "global-rules";
const RULES_PAGE_SIZE = 10;
const RULE_PREVIEW_TEXT_CLASS =
  "max-h-[15rem] min-w-0 overflow-y-auto whitespace-pre-wrap [overflow-wrap:anywhere] pr-2 leading-6";

export function GlobalRulesPage() {
  const [rules, setRules] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  // danger（原因+対処）のみ Banner で常設表示。成功の「瞬間」は toast で 1 回通知する（messaging-spec §9 P1）。
  const [errorText, setErrorText] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState("");
  const [filename, setFilename] = useState("");
  const loadSequence = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const initialLoadStartedRef = useRef(false);
  const cleanupTimerRef = useRef<number | null>(null);

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setErrorText(null);
    const controller = new AbortController();
    loadControllerRef.current = controller;
    try {
      const data = await apiGet<LegacyLearningMaterialData>(
        "/api/nl2sql/legacy-learning-material",
        { signal: controller.signal }
      );
      if (controller.signal.aborted || sequence !== loadSequence.current) return;
      setRules(data.rules);
      setLastLoadedAt(new Date().toISOString());
      if (announce) {
        toast.success(t("globalRules.message.serverLoaded"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setErrorText(err instanceof Error ? err.message : t("globalRules.error.load"));
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

  const importRules = async (file: File) => {
    setFilename(file.name);
    setBusy(true);
    setErrorText(null);
    try {
      const data = await uploadRulesFile(file);
      setRules(data.rules);
      toast.success(t("globalRules.message.imported", { count: data.rules.length }));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : t("globalRules.error.import"));
    } finally {
      setBusy(false);
    }
  };

  const exportRules = async () => {
    setBusy(true);
    setErrorText(null);
    try {
      const response = await apiFetch("/api/nl2sql/legacy-learning-material/rules/export.xlsx", {
        headers: { Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
      });
      if (!response.ok) throw new Error(t("globalRules.error.export"));
      downloadBlob("rules.xlsx", await response.blob());
      toast.success(t("common.action.downloaded"));
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : t("globalRules.error.export"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title={t("globalRules.title")} subtitle={t("globalRules.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <DbObjectManagementStatusBar
          ariaLabel={t("globalRules.status.label")}
          metricColumnsClass="sm:grid-cols-2 xl:grid-cols-3"
          metrics={[
            { label: t("globalRules.status.count"), value: formatNumber(rules.length), emphasis: true },
            { label: t("globalRules.status.lastLoaded"), value: formatDateTime(lastLoadedAt) },
          ]}
          actions={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading}
              onClick={() => void load(true)}
            >
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("glossary.action.refresh")}</span>
            </Button>
          }
        />

        <PageNotice notice={errorText ? { tone: "danger", message: errorText } : null} />

        <DbObjectManagementPanelShell
          id="global-rules-panel"
          labelledBy="global-rules-panel-heading"
          idPrefix={GLOBAL_RULES_ID}
          ariaLabel={t("globalRules.workspace")}
        >
          <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-3">
            <DbObjectPanelHeader
              headingId="global-rules-panel-heading"
              title={t("globalRules.title")}
              description={t("globalRules.hint")}
              icon={Layers3}
              action={
                <StatusBadge
                  variant="neutral"
                  label={t("globalRules.count", { count: rules.length })}
                />
              }
            />
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto] md:items-end">
              <FileInputControl
                label={t("globalRules.import")}
                ariaLabel={t("globalRules.import")}
                accept=".xlsx,.xlsm,.csv,.tsv,.txt"
                filename={filename}
                selectedText={filename}
                emptyText={t("glossary.file.emptyWorkbook")}
                pickText={t("glossary.file.pickWorkbook")}
                replaceText={t("glossary.file.replaceWorkbook")}
                icon="spreadsheet"
                disabled={busy}
                onPick={(file) => void importRules(file)}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="min-h-11 md:self-end"
                loading={busy}
                onClick={() => void exportRules()}
              >
                <Download size={15} aria-hidden="true" />
                <span>{t("globalRules.export")}</span>
              </Button>
            </div>
            {loading && !lastLoadedAt ? (
              <DbManagementLoadingSkeleton
                idPrefix="global-rules"
                ariaLabel={t("globalRules.loading")}
                variant="list"
                rows={6}
              />
            ) : (
              <RulesPreviewTable rules={rules} />
            )}
          </section>
        </DbObjectManagementPanelShell>
      </main>
    </>
  );
}

async function uploadRulesFile(file: File): Promise<LegacyLearningMaterialData> {
  const form = new FormData();
  form.append("file", file);
  const response = await apiFetch("/api/nl2sql/legacy-learning-material/rules/import", {
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
    throw new Error(payload.error || payload.detail || t("globalRules.error.import"));
  }
  return payload.data;
}

function RulesPreviewTable({ rules }: { rules: string[] }) {
  const { page: currentPage, setPage, totalPages, pageItems: visibleRows, range } = usePagination(
    rules,
    RULES_PAGE_SIZE
  );
  const start = range.start === 0 ? 0 : range.start - 1;

  if (rules.length === 0) {
    return (
      <div className="rounded-md border border-border bg-card p-4">
        <EmptyState title={t("globalRules.empty")} hint={t("globalRules.emptyHint")} />
      </div>
    );
  }

  return (
    <div className="grid gap-2" data-testid="global-rules-preview">
      <div className="overflow-hidden rounded-md border border-border bg-card">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed divide-y divide-border text-left text-sm">
            <colgroup>
              <col className="w-12" />
              <col />
            </colgroup>
            <thead className="bg-background text-xs font-semibold uppercase text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 text-right">
                  {t("glossary.preview.rowNumber")}
                </th>
                <th scope="col" className="px-3 py-2">
                  RULE
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70 text-foreground">
              {visibleRows.map((rule, index) => {
                const absoluteIndex = start + index;
                return (
                  <tr key={`${absoluteIndex}-${rule.slice(0, 24)}`}>
                    <td
                      className="px-3 py-2 text-right text-xs tabular-nums text-muted"
                      data-testid="global-rules-row-number"
                    >
                      {absoluteIndex + 1}
                    </td>
                    <td className="min-w-0 px-3 py-2 align-top">
                      <div className={RULE_PREVIEW_TEXT_CLASS} data-testid="global-rules-preview-text">
                        {rule}
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
        ariaLabel={t("globalRules.pagination.label")}
        testId="global-rules-pagination"
      />
    </div>
  );
}
