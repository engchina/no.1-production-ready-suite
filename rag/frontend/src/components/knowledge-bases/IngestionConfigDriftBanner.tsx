"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useDocumentIngestionConfig, useEnqueueDocumentIngestionJob } from "@/lib/queries";
import { toast } from "@/lib/toast";

/**
 * 取込設定ドリフトバナー。文書の取込済み Chunking 戦略が owning KB の現行設定と
 * 異なる場合のみ表示し、現在の設定での再取込を促す。ドリフトが無ければ何も描画しない。
 */
export function IngestionConfigDriftBanner({ documentId }: { documentId: string }) {
  const query = useDocumentIngestionConfig(documentId);
  const reingest = useEnqueueDocumentIngestionJob();

  const data = query.data;
  if (!data || !data.config_drift) return null;

  const handleReingest = () => {
    reingest.mutate(
      { id: documentId, force: true },
      {
        onSuccess: () => toast.success(t("ingestionDrift.toast.queued")),
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("ingestionDrift.error")),
      }
    );
  };

  return (
    <div
      role="status"
      className="flex flex-col gap-3 rounded-lg border border-warning/30 bg-warning-bg/60 p-4 text-warning sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
        <div className="space-y-0.5">
          <p className="text-sm font-semibold">{t("ingestionDrift.title")}</p>
          <p className="text-sm text-foreground">
            {t("ingestionDrift.description", {
              strategy: data.observed_chunking_strategy ?? "-",
              effective: data.effective_chunking_strategy,
            })}
          </p>
        </div>
      </div>
      <Button
        variant="secondary"
        size="sm"
        onClick={handleReingest}
        loading={reingest.isPending}
        className="shrink-0"
      >
        <RefreshCw className="size-4" aria-hidden />
        {t("ingestionDrift.action")}
      </Button>
    </div>
  );
}
