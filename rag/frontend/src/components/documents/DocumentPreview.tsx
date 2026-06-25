"use client";

import { Download, FileQuestion } from "lucide-react";
import { type CSSProperties, type ReactNode, useEffect, useState } from "react";

import { api, type SourcePreviewKind, type SourceProfile } from "@/lib/api";
import {
  type BboxCoordinateMode,
  type BboxOverlayRect,
  type BboxOverlayUnit,
  type BboxPageSize,
  bboxPageAspectRatio,
  formatBboxPercent,
  normalizeBboxForPreview,
} from "@/lib/bbox";
import { t } from "@/lib/i18n";
import { charsetFromContentType, decodeText } from "@/lib/text-decode";
import { Skeleton } from "@/components/ui/skeleton";

type Kind = SourcePreviewKind;
type DocumentContentVariant = "original" | "prepared";

function kindOf(fileName: string, sourceProfile?: SourceProfile | null): Kind {
  if (sourceProfile?.preview_kind) return sourceProfile.preview_kind;
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (["txt", "csv", "md", "json", "log", "html", "htm", "eml"].includes(ext)) return "text";
  if (["doc", "docx", "ppt", "pptx", "xls", "xlsx"].includes(ext)) return "office";
  return "unsupported";
}

export function pdfPreviewUrl(url: string, focusPage?: number | null): string {
  const [baseUrl, existingHash = ""] = url.split("#", 2);
  const params = new URLSearchParams(existingHash);
  if (focusPage) params.set("page", String(focusPage));
  params.set("pagemode", "none");
  params.set("navpanes", "0");
  return `${baseUrl}#${params.toString()}`;
}

/** 原本ファイルのプレビュー（画像 / PDF / テキスト）。 */
export function DocumentPreview({
  documentId,
  fileName,
  variant = "original",
  sourceProfile = null,
  focusPage = null,
  focusBbox = null,
  focusBboxMode = null,
  focusBboxUnit = null,
  focusPageSize = null,
}: {
  documentId: string;
  fileName: string;
  variant?: DocumentContentVariant;
  sourceProfile?: SourceProfile | null;
  focusPage?: number | null;
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPageSize?: BboxPageSize | null;
}) {
  const url =
    variant === "prepared"
      ? api.documentContentUrl(documentId, { variant })
      : api.documentContentUrl(documentId);
  const downloadUrl = api.documentContentUrl(documentId, {
    ...(variant === "prepared" ? { variant } : {}),
    disposition: "attachment",
  });
  const kind = kindOf(fileName, sourceProfile);

  if (kind === "image") {
    return (
      <ImagePreview
        url={url}
        fileName={fileName}
        focusBbox={focusBbox}
        focusBboxMode={focusBboxMode}
        focusBboxUnit={focusBboxUnit}
        focusPage={focusPage}
        focusPageSize={focusPageSize}
      />
    );
  }

  if (kind === "pdf") {
    const pdfUrl = pdfPreviewUrl(url, focusPage);
    return (
      <PreviewFrame
        focusBbox={focusBbox}
        focusBboxMode={focusBboxMode}
        focusBboxUnit={focusBboxUnit}
        focusPage={focusPage}
        focusPageSize={focusPageSize}
      >
        <iframe
          src={pdfUrl}
          title={fileName}
          className="h-[60vh] w-full rounded-md border border-border bg-card"
        />
      </PreviewFrame>
    );
  }

  if (kind === "text" || kind === "html" || kind === "email") {
    return (
      <PreviewFrame
        focusBbox={focusBbox}
        focusBboxMode={focusBboxMode}
        focusBboxUnit={focusBboxUnit}
        focusPage={focusPage}
        focusPageSize={focusPageSize}
      >
        <TextPreview url={url} />
      </PreviewFrame>
    );
  }

  if (kind === "office") {
    return (
      <UnsupportedPreview fileName={fileName} url={downloadUrl} message={t("preview.office")} />
    );
  }

  return (
    <UnsupportedPreview fileName={fileName} url={downloadUrl} message={t("preview.unsupported")} />
  );
}

function ImagePreview({
  url,
  fileName,
  focusBbox,
  focusBboxMode,
  focusBboxUnit,
  focusPage,
  focusPageSize,
}: {
  url: string;
  fileName: string;
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPage?: number | null;
  focusPageSize?: BboxPageSize | null;
}) {
  const [naturalPageSize, setNaturalPageSize] = useState<BboxPageSize | null>(null);
  const pageSize = focusPageSize ?? naturalPageSize;
  return (
      <PreviewFrame
        focusBbox={focusBbox}
        focusBboxMode={focusBboxMode}
        focusBboxUnit={focusBboxUnit}
        focusPage={focusPage}
        focusPageSize={pageSize}
      showContentOverlay
    >
      <div
        className="relative mx-auto w-full overflow-hidden rounded-md border border-border bg-card"
        data-testid="preview-image-surface"
        style={{
          aspectRatio: bboxPageAspectRatio(pageSize),
          maxWidth: imagePreviewMaxWidth(pageSize),
        }}
      >
        <img
          src={url}
          alt={fileName}
          className="absolute inset-0 h-full w-full object-contain"
          onLoad={(event) => {
            const image = event.currentTarget;
            if (image.naturalWidth > 0 && image.naturalHeight > 0) {
              setNaturalPageSize({ width: image.naturalWidth, height: image.naturalHeight });
            }
          }}
        />
      </div>
    </PreviewFrame>
  );
}

function imagePreviewMaxWidth(pageSize?: BboxPageSize | null): string {
  const width = Number(
    pageSize?.rotation === 90 || pageSize?.rotation === 270
      ? pageSize?.height
      : pageSize?.width
  );
  const height = Number(
    pageSize?.rotation === 90 || pageSize?.rotation === 270
      ? pageSize?.width
      : pageSize?.height
  );
  const ratio =
    Number.isFinite(width) && Number.isFinite(height) && width > 0 && height > 0
      ? width / height
      : 1 / 1.414;
  return `min(100%, ${Math.max(12, ratio * 60).toFixed(3)}vh)`;
}

function PreviewFrame({
  children,
  focusBbox,
  focusBboxMode = null,
  focusBboxUnit = null,
  focusPage = null,
  focusPageSize = null,
  showContentOverlay = false,
}: {
  children: ReactNode;
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPage?: number | null;
  focusPageSize?: BboxPageSize | null;
  showContentOverlay?: boolean;
}) {
  const overlayRect = normalizeBboxForPreview(
    focusBbox,
    focusPageSize,
    focusBboxMode,
    focusBboxUnit
  );
  const style = overlayRect ? bboxOverlayStyle(overlayRect) : null;
  const showPreviewOverlay = Boolean(style && !showContentOverlay);
  return (
    <div className="space-y-2">
      {focusBbox && overlayRect ? (
        <BboxLocator
          overlayRect={overlayRect}
          focusPage={focusPage}
          focusPageSize={focusPageSize}
        />
      ) : null}
      {showPreviewOverlay && overlayRect ? (
        <BboxPreviewOverlay
          overlayRect={overlayRect}
          focusPage={focusPage}
          focusPageSize={focusPageSize}
        />
      ) : null}
      <div className="relative">
        {children}
        {style && showContentOverlay ? (
          <span
            aria-hidden
            data-bbox-mode={overlayRect?.coordinateMode}
            data-bbox-unit={overlayRect?.unit}
            data-testid="bbox-content-overlay"
            className="pointer-events-none absolute rounded-sm border-2 border-primary bg-primary/10 shadow-[0_0_0_1px_rgba(255,255,255,0.85)]"
            style={style}
          />
        ) : null}
      </div>
    </div>
  );
}

function BboxPreviewOverlay({
  overlayRect,
  focusPage,
  focusPageSize,
}: {
  overlayRect: BboxOverlayRect;
  focusPage: number | null;
  focusPageSize: BboxPageSize | null;
}) {
  return (
    <div
      aria-label={t("preview.bboxPreviewLabel", { page: focusPage ?? "—" })}
      data-testid="bbox-preview-page"
      className="relative mx-auto max-h-72 w-full max-w-56 overflow-hidden rounded-md border border-primary/40 bg-card shadow-sm"
      role="img"
      style={{ aspectRatio: bboxPageAspectRatio(focusPageSize) }}
    >
      <span
        aria-hidden
        data-bbox-mode={overlayRect.coordinateMode}
        data-bbox-unit={overlayRect.unit}
        data-testid="bbox-preview-overlay"
        className="pointer-events-none absolute rounded-sm border-2 border-primary bg-primary/15 shadow-[0_0_0_1px_rgba(255,255,255,0.9)]"
        style={bboxOverlayStyle(overlayRect)}
      />
    </div>
  );
}

function BboxLocator({
  overlayRect,
  focusPage,
  focusPageSize,
}: {
  overlayRect: BboxOverlayRect;
  focusPage: number | null;
  focusPageSize: BboxPageSize | null;
}) {
  const overlayStyle = bboxOverlayStyle(overlayRect);
  return (
    <div
      role="status"
      aria-live="polite"
      className="rounded-md border border-info/30 bg-info-bg p-3 text-info"
    >
      <div className="grid grid-cols-1 items-center gap-3 sm:grid-cols-[minmax(0,1fr)_4rem]">
        <p className="tnum min-w-0 break-words text-xs">
          {t("preview.bboxFocus", {
            page: focusPage ?? "—",
            x: formatBboxPercent(overlayRect.leftPercent),
            y: formatBboxPercent(overlayRect.topPercent),
            width: formatBboxPercent(overlayRect.widthPercent),
            height: formatBboxPercent(overlayRect.heightPercent),
          })}
        </p>
        <div
          aria-label={t("preview.bboxMapLabel", { page: focusPage ?? "—" })}
          data-testid="bbox-page-map"
          className="relative mx-auto w-16 overflow-hidden rounded-sm border border-info/40 bg-background shadow-sm"
          style={{ aspectRatio: bboxPageAspectRatio(focusPageSize) }}
        >
          <span
            aria-hidden
            data-bbox-mode={overlayRect.coordinateMode}
            data-bbox-unit={overlayRect.unit}
            data-testid="bbox-overlay"
            className="pointer-events-none absolute rounded-[2px] border-2 border-primary bg-primary/15"
            style={overlayStyle}
          />
        </div>
      </div>
    </div>
  );
}

function bboxOverlayStyle(rect: BboxOverlayRect): CSSProperties {
  return {
    left: `${rect.leftPercent}%`,
    top: `${rect.topPercent}%`,
    width: `${rect.widthPercent}%`,
    height: `${rect.heightPercent}%`,
  };
}

function TextPreview({ url }: { url: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setText(null);
    setError(false);
    fetch(url, { signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(String(res.status));
        const charset = charsetFromContentType(res.headers.get("Content-Type"));
        return decodeText(await res.arrayBuffer(), charset);
      })
      .then(setText)
      .catch((e: unknown) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) setError(true);
      });
    return () => controller.abort();
  }, [url]);

  if (error) {
    return (
      <div className="rounded-md border border-border bg-card p-4 text-sm text-muted">
        {t("preview.fetchError")}
      </div>
    );
  }
  if (text === null) return <Skeleton className="h-40 w-full" />;

  return (
    <pre className="max-h-[60vh] overflow-auto rounded-md border border-border bg-card p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-foreground">
      {text}
    </pre>
  );
}

function UnsupportedPreview({
  fileName,
  url,
  message,
}: {
  fileName: string;
  url: string;
  message: string;
}) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-md border border-border bg-card p-4 text-center text-muted">
      <FileQuestion size={24} aria-hidden />
      <p className="text-sm">{message}</p>
      <a
        href={url}
        download={fileName}
        className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-background px-4 text-sm font-medium text-foreground transition-colors hover:bg-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Download size={15} aria-hidden />
        {t("preview.download")}
      </a>
    </div>
  );
}
