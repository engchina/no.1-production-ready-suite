import { useCallback, useEffect, useRef, useState } from "react";
import { Download, RefreshCw } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  DataTable,
  DEFAULT_PAGE_SIZE,
  EmptyState,
  PageHeader,
  Pagination,
  StatusBadge,
  toast,
} from "@engchina/production-ready-ui";

import { ErrorState } from "@/components/StateViews";
import { isAbortError } from "@/lib/api";
import { downloadBlob, downloadFilename } from "@/lib/download";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useRequestScope } from "@/lib/useRequestScope";
import { securityApi } from "./api";
import type { AuditPage } from "./types";

const EMPTY_AUDIT_PAGE: AuditPage = {
  items: [],
  page: 1,
  page_size: DEFAULT_PAGE_SIZE,
  total: 0,
  total_pages: 1,
};

export function SecurityAuditPage() {
  const [pageData, setPageData] = useState<AuditPage>(EMPTY_AUDIT_PAGE);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [loadError, setLoadError] = useState("");
  const requestSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const load = useCallback(async (requestedPage = 1) => {
    const sequence = requestSequence.current + 1;
    requestSequence.current = sequence;
    setLoading(true);
    setLoadError("");
    try {
      await runScopedRequest(async (signal) => {
        const nextPage = await securityApi.auditPage(requestedPage, DEFAULT_PAGE_SIZE, {
          signal,
        });
        if (signal.aborted || sequence !== requestSequence.current) return;
        setPageData(nextPage);
      });
    } catch (cause) {
      if (isAbortError(cause)) {
        return;
      }
      if (sequence === requestSequence.current) {
        setLoadError(t("security.audit.loadError"));
      }
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }, [abortAll, runScopedRequest]);

  useEffect(() => {
    void load(1);
    return () => {
      requestSequence.current += 1;
      abortAll();
    };
  }, [abortAll, load]);

  const exportAudit = async () => {
    setExporting(true);
    try {
      const response = await securityApi.exportAudit();
      if (!response.ok) throw new Error(t("security.audit.exportError"));
      const filename = downloadFilename(response, "nl2sql_audit_logs_last_12_months.xlsx");
      downloadBlob(filename, await response.blob());
      toast.success(t("security.audit.exportStarted"));
    } catch {
      toast.error(t("security.audit.exportError"));
    } finally {
      setExporting(false);
    }
  };

  const rangeStart =
    pageData.total === 0 ? 0 : (pageData.page - 1) * pageData.page_size + 1;
  const rangeEnd = Math.min(pageData.page * pageData.page_size, pageData.total);

  return (
    <>
      <PageHeader
        className="px-4 sm:px-8"
        title={t("nav.securityAudit")}
        subtitle={t("security.audit.subtitle")}
        actions={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void exportAudit()}
              loading={exporting}
            >
              <Download size={14} aria-hidden />
              {t("security.audit.export")}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void load(1)}
              disabled={loading}
            >
              <RefreshCw size={14} aria-hidden />
              {t("security.common.reload")}
            </Button>
          </div>
        }
      />
      <main className="space-y-4 p-4 lg:p-8">
        <Card>
          <CardContent className="pt-6">
            {loadError ? (
              <ErrorState
                message={loadError}
                onRetry={() => void load(pageData.page)}
              />
            ) : (
              <>
                <DataTable
                  loading={loading}
                  dense
                  rows={pageData.items}
                  getRowKey={(row) => row.audit_id}
                  ariaLabel={t("nav.securityAudit")}
                  testId="security-audit-table"
                  empty={<EmptyState title={t("security.common.empty")} />}
                  columns={[
                    {
                      key: "created_at",
                      header: t("security.audit.time"),
                      className: "whitespace-nowrap tabular-nums",
                      render: (row) => formatDateTime(row.created_at),
                    },
                    {
                      key: "event_type",
                      header: t("security.audit.event"),
                      className: "font-mono text-[11px]",
                    },
                    {
                      key: "actor_user_id",
                      header: t("security.audit.actor"),
                      className: "max-w-40 break-all font-mono text-[11px]",
                      render: (row) => row.actor_user_id ?? t("security.common.none"),
                    },
                    {
                      key: "target",
                      header: t("security.audit.target"),
                      render: (row) => (
                        <span>
                          {row.target_type} /{" "}
                          <span className="font-mono text-[11px]">{row.target_id}</span>
                        </span>
                      ),
                    },
                    {
                      key: "outcome",
                      header: t("security.audit.outcome"),
                      render: (row) => (
                        <StatusBadge
                          variant={row.outcome === "SUCCESS" ? "success" : "danger"}
                          label={row.outcome}
                        />
                      ),
                    },
                    {
                      key: "request_id",
                      header: t("security.audit.request"),
                      className: "max-w-40 break-all font-mono text-[11px]",
                    },
                  ]}
                />
                <Pagination
                  className="mt-3"
                  page={pageData.page}
                  totalPages={pageData.total_pages}
                  onPageChange={(nextPage) => void load(nextPage)}
                  summary={t("security.audit.pagination.range", {
                    start: rangeStart,
                    end: rangeEnd,
                    total: pageData.total,
                  })}
                  pageIndicator={t("security.audit.pagination.page", {
                    page: pageData.page,
                    total: pageData.total_pages,
                  })}
                  prevLabel={t("security.audit.pagination.prev")}
                  nextLabel={t("security.audit.pagination.next")}
                  ariaLabel={t("security.audit.pagination.label")}
                  testId="security-audit-pagination"
                />
              </>
            )}
          </CardContent>
        </Card>
      </main>
    </>
  );
}
