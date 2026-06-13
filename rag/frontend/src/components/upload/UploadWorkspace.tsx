"use client";

import { useState } from "react";

import { Dropzone } from "./Dropzone";
import { DocumentWorkspace } from "@/components/documents/DocumentWorkspace";
import { PageHeader } from "@/components/PageHeader";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/StateViews";
import { ApiError, type UploadResult } from "@/lib/api";
import { useUploadDocument } from "@/lib/queries";
import { t } from "@/lib/i18n";

/** アップロード → 分析 → 登録 を1画面で進めるワークスペース。 */
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
