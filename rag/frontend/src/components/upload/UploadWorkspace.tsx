"use client";

import { Cloud, HardDrive, Settings } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Dropzone } from "./Dropzone";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/StateViews";
import { ApiError, type UploadResult, type UploadStorageSettingsData } from "@/lib/api";
import { useUploadDocument, useUploadStorageSettings } from "@/lib/queries";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

/** アップロード → 取込 → RAG 索引化を1画面で進めるワークスペース。 */
export function UploadWorkspace() {
  const [uploaded, setUploaded] = useState<UploadResult | null>(null);
  const upload = useUploadDocument();

  const reset = () => {
    setUploaded(null);
    upload.reset();
  };

  const handleFile = (file: File) => {
    upload.mutate(file, { onSuccess: (result) => setUploaded(result) });
  };

  return (
    <div>
      <PageHeader title={t("nav.upload")} subtitle={t("upload.subtitle")} />
      <div className="space-y-6 p-8">
        {!uploaded ? (
          <>
            <UploadStorageNotice />
            <Dropzone onFile={handleFile} disabled={upload.isPending} />
            {upload.isPending ? (
              <p className="text-sm text-muted" role="status">
                {t("upload.uploading")}
              </p>
            ) : null}
            {upload.isError ? (
              <ErrorState
                message={
                  upload.error instanceof ApiError
                    ? upload.error.message
                    : "アップロードに失敗しました。"
                }
              />
            ) : null}
          </>
        ) : (
          <>
            <DocumentWorkspace documentId={uploaded.id} />
            <Button variant="ghost" onClick={reset}>
              {t("upload.uploadAnother")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function UploadStorageNotice() {
  const query = useUploadStorageSettings();

  if (query.isPending || query.isError || !query.data) return null;

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border bg-card px-4 py-3 text-sm text-foreground md:flex-row md:items-center md:justify-between">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
          {query.data.backend === "oci" ? (
            <Cloud size={18} aria-hidden />
          ) : (
            <HardDrive size={18} aria-hidden />
          )}
        </div>
        <div>
          <p className="font-medium">
            {t("upload.storageNotice.title")}: {storageBackendLabel(query.data.backend)}
          </p>
          <p className="mt-1 break-all text-xs text-muted">
            {storageTarget(query.data)}
          </p>
        </div>
      </div>
      <Link
        to={APP_ROUTES.settingsUploadStorage}
        className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-info-bg"
      >
        <Settings size={14} aria-hidden />
        {t("upload.storageNotice.settings")}
      </Link>
    </div>
  );
}

function storageBackendLabel(backend: UploadStorageSettingsData["backend"]): string {
  return backend === "oci"
    ? t("settings.uploadStorage.backend.oci")
    : t("settings.uploadStorage.backend.local");
}

function storageTarget(settings: UploadStorageSettingsData): string {
  if (settings.backend === "oci") {
    return settings.object_storage_namespace && settings.object_storage_bucket
      ? `${settings.object_storage_namespace}/${settings.object_storage_bucket}`
      : t("upload.storageNotice.unset");
  }
  return settings.local_storage_dir || t("upload.storageNotice.unset");
}
