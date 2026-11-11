"use client";

import { useCallback, useMemo } from "react";

import {
  api,
  ApiError,
  type DocumentPreprocessArtifact,
  type SourcePreviewKind,
  type SourceProfile,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useDocumentPreviewPages } from "@/lib/queries";
import {
  DocumentPreview as SharedDocumentPreview,
  SourceVersionStaleNotice,
  isSourceVersionStale,
  documentPreviewLabels,
  type BboxCoordinateMode,
  type BboxOverlayUnit,
  type BboxPageSize,
  type DocumentPreviewKind,
  type DocumentTextRange,
  type DocumentPreviewPagesState,
  type PreviewHighlight,
  type PreviewSizing,
  type PreviewViewerPage,
} from "@production-ready/ui";

type Kind = SourcePreviewKind;
type DocumentContentVariant = "original" | "prepared";

/** 原文のプレビューの文言（3 製品で同じ。再試行だけ RAG の共通の文言）。 */
const PREVIEW_LABELS = documentPreviewLabels({ retry: t("common.retry") });

function kindOf(fileName: string, sourceProfile?: SourceProfile | null): Kind {
  if (sourceProfile?.preview_kind) return sourceProfile.preview_kind;
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  if (["png", "jpg", "jpeg", "gif", "webp", "bmp"].includes(ext)) return "image";
  if (ext === "pdf") return "pdf";
  if (["txt", "csv", "tsv", "md", "markdown", "json", "xml", "log", "html", "htm", "eml"].includes(ext)) return "text";
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
 * 原本ファイルのプレビュー（画像 / PDF / テキスト）。表示は 3 製品で共通の `DocumentPreview`（#2389）で、
 * ここは RAG の文書の API（原本・処理後のファイル・ページの一覧と画像）をつなぐ。
 *
 * 画像と PDF（Office は変換済み PDF）はページ画像のビューアで表示し、回転・拡大縮小・フィット・パン・ページ送りと
 * bbox の強調を付ける（#349）。PDF のページ画像を描けないときは、ブラウザの PDF 表示（iframe）と位置の案内に戻す。
 * 高さは `sizing` で決める（`fill` は親に合わせる・`page` は 1 ページ全体が幅に合わせて入る。#559）。
 */
export function DocumentPreview({
  documentId,
  recipeId = null,
  chunkSetId = null,
  fileName,
  variant = "original",
  sourceProfile = null,
  preparedArtifact = null,
  preparedPdf,
  showFallbackDownload = true,
  focusPage = null,
  onPageChange,
  onTextRangeChange,
  focusElementId = null,
  focusSectionId = null,
  focusRequestKey = null,
  focusBbox = null,
  focusBboxMode = null,
  focusBboxUnit = null,
  focusPageSize = null,
  highlights = null,
  focusText = null,
  sizing = "fill",
  className,
}: {
  documentId: string;
  recipeId?: string | null;
  /** 引用の版。渡すと処理後のファイルをその版のものにし、消えた版は版の失効の案内にする（#4168）。 */
  chunkSetId?: string | null;
  fileName: string;
  variant?: DocumentContentVariant;
  sourceProfile?: SourceProfile | null;
  preparedArtifact?: DocumentPreprocessArtifact | null;
  /** 引用CSの原本metadataから確認したPDF種別。現在のレシピは代用しない。 */
  preparedPdf?: boolean;
  showFallbackDownload?: boolean;
  focusPage?: number | null;
  onPageChange?: (page: number) => void;
  onTextRangeChange?: (range: DocumentTextRange) => void;
  focusElementId?: string | null;
  focusSectionId?: string | null;
  focusRequestKey?: string | null;
  focusBbox?: number[] | null;
  focusBboxMode?: BboxCoordinateMode | null;
  focusBboxUnit?: BboxOverlayUnit | null;
  focusPageSize?: BboxPageSize | null;
  /** 強調する領域（要素の表示領域など）。省略時は focusBbox 1 つを強調する。 */
  highlights?: PreviewHighlight[] | null;
  /** テキストの原本で、引用した箇所（根拠の抜粋）へ移って強調する（#2512）。 */
  focusText?: string | null;
  sizing?: PreviewSizing;
  className?: string;
}) {
  const contentUrl = (
    options: { variant?: DocumentContentVariant; disposition?: "inline" | "attachment" } = {}
  ) =>
    recipeId
      ? api.documentRecipeContentUrl(documentId, recipeId, { ...options, chunkSetId })
      : api.documentContentUrl(documentId, options);
  const url = variant === "prepared" ? contentUrl({ variant }) : contentUrl();
  const downloadUrl = contentUrl({
    ...(variant === "prepared" ? { variant } : {}),
    disposition: "attachment",
  });
  const kind = kindOf(fileName, sourceProfile);
  // Office の原本（docx / pptx / xlsx 等）はブラウザで描けないので、preprocess が作った変換済み PDF があれば
  // それを表示する（抽出 bbox も変換済み PDF の座標系で合う）。
  const officeAsPdf = kind === "office" && (preparedPdf ?? isPreparedPdfArtifact(preparedArtifact)) && variant !== "prepared";
  const pdfVariant: DocumentContentVariant = officeAsPdf ? "prepared" : variant;
  const pdfUrl = officeAsPdf ? contentUrl({ variant: "prepared" }) : url;
  const sharedKind: DocumentPreviewKind =
    kind === "pdf" || officeAsPdf
      ? "pdf"
      : kind === "image"
        ? "image"
        : kind === "text" || kind === "html" || kind === "email"
          ? "text"
          : "unsupported";
  const { pages, versionStale } = useDocumentPdfPages(
    documentId,
    recipeId,
    chunkSetId,
    pdfVariant,
    sharedKind === "pdf",
  );
  const loadTextRange = useCallback((offset: number, signal: AbortSignal) =>
    api.documentTextWindow(documentId, recipeId, { offset, limit: 8192, variant,
      focus: offset === 0 ? focusText : null,
      element_id: offset === 0 ? focusElementId : null, section_id: offset === 0 ? focusSectionId : null }, signal), [documentId, recipeId, variant, focusText, focusElementId, focusSectionId]);

  // やり直しても開けないので、再試行の案内や iframe の表示に戻さない。
  if (versionStale) return <SourceVersionStaleNotice />;

  return (
    <SharedDocumentPreview
      // 文書・レシピ・処理前後を切り替えたら、倍率・回転を初期化する。
      key={sharedKind === "pdf" ? pdfUrl : url}
      kind={sharedKind}
      fileName={fileName}
      imageUrl={url}
      pages={pages}
      iframeUrl={pdfPreviewUrl(pdfUrl, focusPage)}
      loadTextRange={loadTextRange}
      textKey={`${url}:${focusElementId ?? ""}:${focusSectionId ?? ""}`}
      focusText={focusText}
      // 表示できない形式（Office の原本・処理後の JSONL など）は、形式によらず共通の案内の 1 文にする（#2679）。
      downloadUrl={showFallbackDownload ? downloadUrl : null}
      focusPage={focusPage}
      onPageChange={onPageChange}
      onTextRangeChange={onTextRangeChange}
      focusRequestKey={focusRequestKey}
      focusBbox={focusBbox}
      focusBboxMode={focusBboxMode}
      focusBboxUnit={focusBboxUnit}
      focusPageSize={focusPageSize}
      highlights={highlights}
      sizing={sizing}
      className={className}
      labels={PREVIEW_LABELS}
    />
  );
}

/** PDF のページの一覧（RAG の preview-pages API）→ 共通のプレビューのページの状態。 */
function useDocumentPdfPages(
  documentId: string,
  recipeId: string | null,
  chunkSetId: string | null,
  variant: DocumentContentVariant,
  enabled: boolean
): { pages: DocumentPreviewPagesState; versionStale: boolean } {
  const pagesQuery = useDocumentPreviewPages(documentId, { recipeId, chunkSetId, variant, enabled });
  const pages = useMemo<PreviewViewerPage[]>(
    () =>
      (pagesQuery.data?.pages ?? []).map((page) => ({
        pageNumber: page.page_number,
        points: { width: page.width, height: page.height },
        imageUrl: (dpi: number) =>
          api.documentPreviewPageImageUrl(documentId, page.page_number, {
            recipeId,
            chunkSetId,
            variant,
            dpi,
          }),
      })),
    [documentId, pagesQuery.data?.pages, recipeId, chunkSetId, variant]
  );
  const versionStale = isSourceVersionStale(pagesQuery.error);
  if (pagesQuery.isPending) return { pages: { status: "loading" }, versionStale };
  if (isMissingFileError(pagesQuery.error)) {
    // ファイルが保存先に無いときは、iframe に 404 の本文を出さず、理由と対処を出す（#1210）。
    return {
      pages: { status: "error", error: pagesQuery.error, onRetry: () => void pagesQuery.refetch() },
      versionStale,
    };
  }
  // ほかの失敗（描けないファイル・古い backend）は iframe の表示に戻す。
  if (pagesQuery.isError) return { pages: { status: "fallback" }, versionStale };
  return { pages: { status: "ready", pages }, versionStale };
}

/** 文書のファイルが保存先に無い（backend の `error_code`。#1210）。 */
const DOCUMENT_FILE_MISSING_CODE = "RAG_DOCUMENT_FILE_MISSING";

/** ページの一覧の失敗のうち、ファイルが保存先に無いことによる失敗か。ほかの失敗は iframe の表示に戻す。 */
function isMissingFileError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.errorCode === DOCUMENT_FILE_MISSING_CODE;
}
