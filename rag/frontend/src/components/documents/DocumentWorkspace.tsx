"use client";

import { AlertTriangle, CheckCircle2, FileText } from "lucide-react";

import { DocumentPreview } from "./DocumentPreview";
import { DocumentExtraction } from "./DocumentExtraction";
import { FlowStepper } from "@/components/upload/FlowStepper";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { useDocument, useIngestDocument } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { formatBytes, formatDateTime } from "@/lib/format";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 文書プレビュー作業領域：原本プレビュー｜抽出本文＋取込アクション。 */
export function DocumentWorkspace({ documentId }: { documentId: string }) {
  const query = useDocument(documentId);
  const ingest = useIngestDocument();

  if (query.isPending) return <Skeleton className="h-80 w-full rounded-lg" />;
  if (query.isError) {
    return (
      <ErrorState
        message={errorMessage(query.error, t("workspace.notFound"))}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const doc = query.data;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <FileText size={18} className="text-primary" aria-hidden />
            <span className="truncate" title={doc.file_name}>
              {doc.file_name}
            </span>
          </CardTitle>
          <StatusBadge status={doc.status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {doc.duplicate_of_document_id ? (
          <div className="flex items-start gap-2 rounded-md bg-warning-bg/60 px-3 py-2 text-sm text-warning">
            <AlertTriangle size={16} className="mt-0.5 shrink-0" aria-hidden />
            {t("upload.duplicate")}
          </div>
        ) : null}

        <FlowStepper status={doc.status} />

        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">{t("flow.size")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatBytes(doc.file_size_bytes)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("flow.uploadedAt")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatDateTime(doc.uploaded_at)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("flow.indexedAt")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatDateTime(doc.indexed_at)}
            </dd>
          </div>
        </dl>

        {doc.error_message ? (
          <div className="rounded-md bg-danger-bg/50 px-3 py-2 text-sm text-danger" role="alert">
            {doc.error_message}
          </div>
        ) : null}
        {ingest.isError ? (
          <div className="rounded-md bg-danger-bg/50 px-3 py-2 text-sm text-danger" role="alert">
            {errorMessage(ingest.error, t("flow.ingestFailed"))}
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">{t("flow.preview")}</h3>
            <DocumentPreview documentId={documentId} fileName={doc.file_name} />
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">
              {t("flow.extraction.title")}
            </h3>
            <DocumentExtraction extraction={doc.extraction} />
          </section>
        </div>

        {doc.status === "INDEXED" ? (
          <div className="flex items-center gap-2 rounded-md bg-success-bg/60 px-3 py-2 text-sm font-medium text-success">
            <CheckCircle2 size={16} aria-hidden />
            {t("flow.indexed")}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          {(doc.status === "UPLOADED" || doc.status === "ERROR") && (
            <Button onClick={() => ingest.mutate({ id: documentId })} loading={ingest.isPending}>
              {ingest.isPending ? t("action.ingesting") : t("action.ingest")}
            </Button>
          )}
          {doc.status === "INDEXED" && (
            <Button
              variant="secondary"
              onClick={() => ingest.mutate({ id: documentId, force: true })}
              loading={ingest.isPending}
            >
              {t("action.reingest")}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
