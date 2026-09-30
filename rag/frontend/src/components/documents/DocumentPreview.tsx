"use client";

import { Download, FileQuestion } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import {
  api,
  notifyResponseAuthStatus,
  type DocumentPreprocessArtifact,
  type SourcePreviewKind,
  type SourceProfile,
} from "@/lib/api";
import {
  type BboxCoordinateMode,
  type BboxOverlayRect,
  type BboxOverlayUnit,
  type BboxPageSize,
  type PreviewHighlight,
  bboxPageAspectRatio,
  buildPreviewHighlights,
  formatBboxPercent,
  normalizeBboxForPreview,
} from "@/lib/bbox";
import { t } from "@/lib/i18n";
import { useDocumentPreviewPages } from "@/lib/queries";
import { charsetFromContentType, decodeText } from "@/lib/text-decode";
import {
  buttonVariants,
  cn,
  Skeleton,
  TimedLoadingState,
} from "@engchina/production-ready-ui";

import {
  bboxOverlayStyle,
  PreviewViewer,
  type PreviewSizing,
  type PreviewViewerPage,
} from "./PreviewViewer";

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

export function isPreparedPdfArtifact(
  artifact:
    | Pick<DocumentPreprocessArtifact, "object_storage_path" | "content_type" | "file_name">
    | null
    | undefined
): boolean {
  if (!artifact?.object_storage_path) return false;
  const contentType = artifact.content_type?.split(";", 1)[0].trim().toLowerCase();
  return contentType
    ? contentType === "application/pdf"
    : artifact.file_name.toLowerCase().endsWith(".pdf");
}

/**
 * 原本ファイルのプレビュー（画像 / PDF / テキスト）。
 *
 * 画像と PDF（Office は変換済み PDF）はページ画像のビューア（PreviewViewer）で表示し、回転・拡大縮小・
 * フィット・パン・ページ送りと bbox の強調を付ける（#349）。PDF のページ画像を描けないときは、
 * 従来どおりブラウザの PDF 表示（iframe）と位置の案内に戻す。
 *
 * 高さは `sizing` で決める（#559）。
 * - `fill`（既定）: 親に合わせる（呼び出し側が 1 画面分などの高さを与える。引用のダイアログ）。
 * - `page`: 1 ページ全体が幅に合わせて入る高さ（文書詳細）。ページ画像のビューアはページの縦横比から、
 *   ページの寸法が分からない表示（読み込み中・iframe の PDF・テキスト）は A4 縦の縦横比（CSS の aspect-ratio）で
 *   領域を取り、読み込みの前後で高さが大きく跳ねないようにする。親が高さを足す（1 画面分の下限など）と伸びて埋める。
 */
export function DocumentPreview({
  documentId,
  recipeId = null,
  fileName,
  variant = "original",
  sourceProfile = null,
  preparedArtifact = null,
  showFallbackDownload = true,
  focusPage = null,
  focusBbox = null,
  focusBboxMode = null,
  focusBboxUnit = null,
  focusPageSize = null,
  highlights = null,
  sizing = "fill",
  className,
}: {
  documentId: string;
  recipeId?: string | null;
  fileName: string;
  variant?: DocumentContentVariant;
  sourceProfile?: SourceProfile | null;
  preparedArtifact?: DocumentPreprocessArtifact | null;
  showFallbackDownload?: boolean;
  focusPage?: number | null;
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPageSize?: BboxPageSize | null;
  /** 強調する領域（DocRAG の表示領域など）。省略時は focusBbox 1 つを強調する。 */
  highlights?: PreviewHighlight[] | null;
  sizing?: PreviewSizing;
  className?: string;
}) {
  const contentUrl = (
    options: { variant?: DocumentContentVariant; disposition?: "inline" | "attachment" } = {}
  ) =>
    recipeId
      ? api.documentRecipeContentUrl(documentId, recipeId, options)
      : api.documentContentUrl(documentId, options);
  const url =
    variant === "prepared"
      ? contentUrl({ variant })
      : contentUrl();
  const downloadUrl = contentUrl({
    ...(variant === "prepared" ? { variant } : {}),
    disposition: "attachment",
  });
  const kind = kindOf(fileName, sourceProfile);
  const focus = { focusBbox, focusBboxMode, focusBboxUnit, focusPage, focusPageSize };
  const viewerClassName = cn(sizing === "page" ? "flex-auto" : "h-full min-h-80", className);
  const effectiveHighlights =
    highlights ??
    buildPreviewHighlights({ focusPage, focusBbox, focusBboxMode, focusBboxUnit, focusPageSize });

  if (kind === "image") {
    return (
      <PreviewViewer
        key={url}
        kind="image"
        sizing={sizing}
        className={viewerClassName}
        fileName={fileName}
        pages={[{ pageNumber: 1, imageUrl: () => url }]}
        focusPage={1}
        // 画像は 1 ページとして扱う（ページ番号の無い・1 以外の値でも同じ画像に重ねる）。
        highlights={effectiveHighlights.map((highlight) => ({ ...highlight, page: null }))}
      />
    );
  }

  if (kind === "pdf") {
    return (
      <PdfPagesPreview
        key={url}
        documentId={documentId}
        recipeId={recipeId}
        variant={variant}
        fileName={fileName}
        iframeUrl={pdfPreviewUrl(url, focusPage)}
        highlights={effectiveHighlights}
        sizing={sizing}
        className={className}
        {...focus}
      />
    );
  }

  if (kind === "text" || kind === "html" || kind === "email") {
    return (
      <PreviewFrame className={className} sizing={sizing} {...focus}>
        <TextPreview url={url} />
      </PreviewFrame>
    );
  }

  if (kind === "office") {
    // 原本(docx/pptx/xlsx 等)はブラウザで直接描画できないため、preprocess が生成した
    // 変換済 PDF があればそれを表示する(抽出 bbox も prepared PDF 座標系で整合)。
    if (isPreparedPdfArtifact(preparedArtifact) && variant !== "prepared") {
      const preparedUrl = contentUrl({ variant: "prepared" });
      return (
        <PdfPagesPreview
          key={preparedUrl}
          documentId={documentId}
          recipeId={recipeId}
          variant="prepared"
          fileName={fileName}
          iframeUrl={pdfPreviewUrl(preparedUrl, focusPage)}
          highlights={effectiveHighlights}
          sizing={sizing}
          className={className}
          {...focus}
        />
      );
    }
    return (
      <UnsupportedPreview
        fileName={fileName}
        url={downloadUrl}
        message={t("preview.office")}
        showDownload={showFallbackDownload}
      />
    );
  }

  return (
    <UnsupportedPreview
      fileName={fileName}
      url={downloadUrl}
      message={t("preview.unsupported")}
      showDownload={showFallbackDownload}
    />
  );
}

type FocusProps = {
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPage?: number | null;
  focusPageSize?: BboxPageSize | null;
};

/** PDF をページ画像で表示する。ページ一覧を取れないとき（描けないファイル・古い backend）は iframe に戻す。 */
function PdfPagesPreview({
  documentId,
  recipeId,
  variant,
  fileName,
  iframeUrl,
  highlights,
  sizing,
  className,
  ...focus
}: FocusProps & {
  documentId: string;
  recipeId: string | null;
  variant: DocumentContentVariant;
  fileName: string;
  iframeUrl: string;
  highlights: PreviewHighlight[];
  sizing: PreviewSizing;
  className?: string;
}) {
  const pagesQuery = useDocumentPreviewPages(documentId, { recipeId, variant });
  const pages = useMemo<PreviewViewerPage[]>(
    () =>
      (pagesQuery.data?.pages ?? []).map((page) => ({
        pageNumber: page.page_number,
        points: { width: page.width, height: page.height },
        imageUrl: (dpi: number) =>
          api.documentPreviewPageImageUrl(documentId, page.page_number, {
            recipeId,
            variant,
            dpi,
          }),
      })),
    [documentId, pagesQuery.data?.pages, recipeId, variant]
  );

  if (pagesQuery.isPending) {
    return (
      <TimedLoadingState
        label={t("preview.viewer.loading")}
        placement="panel"
        testId="preview-loading"
        className={cn(
          sizing === "page" ? "flex-auto" : "h-full min-h-80",
          "grid-rows-[auto_minmax(0,1fr)]",
          className
        )}
      >
        {/* `page` はページの寸法が届く前なので A4 縦で領域を取る（届いた後のビューアとほぼ同じ高さ。#559）。 */}
        <Skeleton
          className={cn(
            "w-full",
            sizing === "page" ? PAGE_SIZED_FRAME_CLASS : "h-full min-h-60"
          )}
        />
      </TimedLoadingState>
    );
  }
  if (pagesQuery.isError || pages.length === 0) {
    return (
      <PreviewFrame className={className} sizing={sizing} {...focus}>
        <iframe
          src={iframeUrl}
          title={fileName}
          className="h-full min-h-80 w-full rounded-md border border-border bg-surface"
        />
      </PreviewFrame>
    );
  }
  return (
    <PreviewViewer
      kind="pdf"
      sizing={sizing}
      className={cn(sizing === "page" ? "flex-auto" : "h-full min-h-80", className)}
      fileName={fileName}
      pages={pages}
      focusPage={focus.focusPage ?? null}
      highlights={highlights}
    />
  );
}

/**
 * ページの寸法が分からない表示（読み込み中・iframe の PDF・テキスト）の `page` の高さ（#559）。
 * A4 縦（210 : 297）の縦横比で幅から高さを決め、上限はページ画像のビューアと同じ 2 画面分にする。
 */
const PAGE_SIZED_FRAME_CLASS = "aspect-[210/297] max-h-[calc(200dvh-10rem)]";

/** ページ画像で表示できないとき（iframe の PDF・テキスト）に、位置の案内を上に添える。 */
function PreviewFrame({
  children,
  focusBbox,
  focusBboxMode = null,
  focusBboxUnit = null,
  focusPage = null,
  focusPageSize = null,
  sizing = "fill",
  className,
}: FocusProps & { children: ReactNode; sizing?: PreviewSizing; className?: string }) {
  const overlayRect = normalizeBboxForPreview(
    focusBbox,
    focusPageSize,
    focusBboxMode,
    focusBboxUnit
  );
  return (
    <div
      className={cn(
        "flex flex-col gap-2",
        sizing === "page" ? "flex-auto" : "h-full min-h-0",
        className
      )}
    >
      {focusBbox && overlayRect ? (
        <BboxLocator
          overlayRect={overlayRect}
          focusPage={focusPage}
          focusPageSize={focusPageSize}
        />
      ) : null}
      {overlayRect ? (
        <BboxPreviewOverlay
          overlayRect={overlayRect}
          focusPage={focusPage}
          focusPageSize={focusPageSize}
        />
      ) : null}
      {sizing === "page" ? (
        // A4 縦の縦横比で領域を取り、中身（iframe・テキスト）はその中いっぱいに置く（テキストは中でスクロールする）。
        <div className={cn("relative w-full flex-auto", PAGE_SIZED_FRAME_CLASS)}>
          <div className="absolute inset-0 flex flex-col">{children}</div>
        </div>
      ) : (
        <div className="relative min-h-0 flex-1">{children}</div>
      )}
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
      className="relative mx-auto max-h-72 w-full max-w-56 overflow-hidden rounded-md border border-accent-emphasis bg-surface shadow-sm"
      role="img"
      style={{ aspectRatio: bboxPageAspectRatio(focusPageSize) }}
    >
      <span
        aria-hidden
        data-bbox-mode={overlayRect.coordinateMode}
        data-bbox-unit={overlayRect.unit}
        data-testid="bbox-preview-overlay"
        className="pointer-events-none absolute rounded-sm border-2 border-accent-emphasis bg-accent-muted shadow-[0_0_0_1px_rgba(255,255,255,0.9)]"
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
      className="rounded-md border border-info-border bg-info-subtle p-3 text-info-fg"
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
          className="relative mx-auto w-16 overflow-hidden rounded-sm border border-info-border bg-surface-sunken shadow-sm"
          style={{ aspectRatio: bboxPageAspectRatio(focusPageSize) }}
        >
          <span
            aria-hidden
            data-bbox-mode={overlayRect.coordinateMode}
            data-bbox-unit={overlayRect.unit}
            data-testid="bbox-overlay"
            className="pointer-events-none absolute rounded-sm border-2 border-accent-emphasis bg-accent-muted"
            style={overlayStyle}
          />
        </div>
      </div>
    </div>
  );
}

function TextPreview({ url }: { url: string }) {
  // 取得結果は URL ごとに持つ。URL が変わった直後は前の結果を使わず、読込中として扱う。
  const [result, setResult] = useState<{ url: string; text: string | null; error: boolean } | null>(
    null
  );
  const text = result?.url === url ? result.text : null;
  const error = result?.url === url ? result.error : false;

  useEffect(() => {
    const controller = new AbortController();
    fetch(url, { signal: controller.signal, credentials: "same-origin" })
      .then(async (res) => {
        if (!res.ok) {
          // セッション切れはログインへ。経路の権限拒否以外の 403 はプレビュー内の失敗表示にとどめる（#224）。
          notifyResponseAuthStatus(res);
          throw new Error(String(res.status));
        }
        const charset = charsetFromContentType(res.headers.get("Content-Type"));
        return decodeText(await res.arrayBuffer(), charset);
      })
      .then((decoded) => setResult({ url, text: decoded, error: false }))
      .catch((e: unknown) => {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          setResult({ url, text: null, error: true });
        }
      });
    return () => controller.abort();
  }, [url]);

  if (error) {
    return (
      <div className="rounded-md border border-border bg-surface p-4 text-sm text-fg-muted">
        {t("preview.fetchError")}
      </div>
    );
  }
  if (text === null) {
    return (
      <TimedLoadingState
        label={t("preview.text.loading")}
        operationKey="preview-text-load"
        framed={false}
        testId="preview-text-loading"
      >
        <Skeleton className="h-40 w-full" />
      </TimedLoadingState>
    );
  }

  return (
    <pre className="h-full max-h-full min-h-40 overflow-auto rounded-md border border-border bg-surface p-4 text-sm leading-relaxed whitespace-pre-wrap break-words text-fg">
      {text}
    </pre>
  );
}

function UnsupportedPreview({
  fileName,
  url,
  message,
  showDownload,
}: {
  fileName: string;
  url: string;
  message: string;
  showDownload: boolean;
}) {
  return (
    <div className="flex min-h-40 flex-col items-center justify-center gap-3 rounded-md border border-border bg-surface p-4 text-center text-fg-muted">
      <FileQuestion size={24} aria-hidden />
      <p className="text-sm">{message}</p>
      {showDownload ? (
        <a
          href={url}
          download={fileName}
          className={buttonVariants({ variant: "secondary", size: "md" })}
        >
          <Download size={16} aria-hidden />
          {t("preview.download")}
        </a>
      ) : null}
    </div>
  );
}
