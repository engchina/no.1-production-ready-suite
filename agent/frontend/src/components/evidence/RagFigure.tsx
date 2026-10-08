import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ImageIcon, RefreshCw } from "lucide-react";
import {
  Banner,
  Button,
  SideSheet,
  Skeleton,
  TimedLoadingState,
  apiErrorMessage,
  cn,
} from "@production-ready/ui";

import { agentApi } from "@/lib/api";
import { t } from "@/lib/i18n";
import type { RagFigureRef } from "@/lib/rag-evidence";

/**
 * RAG の図の根拠を開く導線（#1311）。チャットの出典と実行の詳細の根拠で使う。
 *
 * 押すと右の side sheet を開き、Agent の backend から短命の URL（RAG が利用者・根拠・版に縛って署名し、
 * 読むたびに権限・版を確かめ直す）を取り、その URL の画像を出す。URL は保存せず、開くたびに取り直す
 * （閉じると query を捨てる）。画像を読めないとき（期限切れ・資料の更新・権限）は取り直しの操作を出す。
 */
export function RagFigureButton({
  runId,
  figure,
  title,
  location,
  testId,
}: {
  runId: string;
  figure: RagFigureRef;
  /** 根拠の文書名（見出しと代替テキストに使う）。 */
  title: string;
  /** 根拠の場所（頁・節）。代替テキストに使う。 */
  location: string | null;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        icon={ImageIcon}
        aria-haspopup="dialog"
        aria-label={t("evidence.figureOpenLabel", { title })}
        onClick={() => setOpen(true)}
        data-testid={testId}
      >
        {t("evidence.figureOpen")}
      </Button>
      <SideSheet
        open={open}
        onClose={() => setOpen(false)}
        title={t("evidence.figureTitle", { title })}
        closeLabel={t("evidence.figureClose")}
        side="right"
        size="wide"
        data-testid={testId ? `${testId}-sheet` : undefined}
      >
        {/* 閉じている間は描かない（開くたびに短命の URL を取り直す）。 */}
        {open ? <FigureViewer runId={runId} figure={figure} title={title} location={location} /> : null}
      </SideSheet>
    </>
  );
}

type ImageState = "loading" | "loaded" | "error";

function FigureViewer({
  runId,
  figure,
  title,
  location,
}: {
  runId: string;
  figure: RagFigureRef;
  title: string;
  location: string | null;
}) {
  const figureUrl = useQuery({
    queryKey: ["run-figure-url", runId, figure.documentId, figure.chunkId],
    queryFn: () => agentApi.getRunFigureUrl(runId, figure.documentId, figure.chunkId),
    // URL は短命（5 分）なので cache しない。
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
  });
  // 画像の読み込みの状態（URL ごと）。URL を取り直すと読み込みからやり直す。
  const [image, setImage] = useState<{ url: string | null; state: ImageState }>({ url: null, state: "loading" });
  // 取り直しの回数（同じ URL が返っても img を作り直して読み込み直す）。
  const [attempt, setAttempt] = useState(0);
  const url = figureUrl.data?.url ?? null;
  const imageState: ImageState = image.url === url ? image.state : "loading";
  const reload = () => {
    setImage({ url: null, state: "loading" });
    setAttempt((value) => value + 1);
    void figureUrl.refetch();
  };
  const alt = location
    ? t("evidence.figureAlt", { title, location })
    : t("evidence.figureAltNoLocation", { title });

  if (figureUrl.isError) {
    return (
      <FigureFailure
        title={t("evidence.figureFailed")}
        message={apiErrorMessage(figureUrl.error, t("evidence.figureFailedFallback"))}
        onReload={reload}
        reloading={figureUrl.isFetching}
      />
    );
  }
  const loading = !url || figureUrl.isFetching || imageState === "loading";
  return (
    <div className="space-y-3" data-testid="rag-figure-viewer">
      <p className="text-xs text-fg-muted">{t("evidence.figureNote")}</p>
      {imageState === "error" && !figureUrl.isFetching ? (
        <FigureFailure
          title={t("evidence.figureFailed")}
          message={t("evidence.figureImageFailed")}
          onReload={reload}
          reloading={false}
        />
      ) : null}
      {loading && imageState !== "error" ? (
        <TimedLoadingState label={t("evidence.figureLoading")} testId="rag-figure-loading">
          <Skeleton className="h-64 w-full" />
        </TimedLoadingState>
      ) : null}
      {url && !figureUrl.isFetching && imageState !== "error" ? (
        <img
          key={`${url}#${attempt}`}
          src={url}
          alt={alt}
          // RAG の URL（トークン）を Referer で他へ渡さない。
          referrerPolicy="no-referrer"
          decoding="async"
          className={cn(
            "block h-auto max-w-full rounded-sm border border-border bg-surface",
            imageState === "loaded" ? null : "sr-only"
          )}
          onLoad={() => setImage({ url, state: "loaded" })}
          onError={() => setImage({ url, state: "error" })}
          data-testid="rag-figure-image"
        />
      ) : null}
    </div>
  );
}

function FigureFailure({
  title,
  message,
  onReload,
  reloading,
}: {
  title: string;
  message: string;
  onReload: () => void;
  reloading: boolean;
}) {
  return (
    <div className="space-y-2" data-testid="rag-figure-error">
      <Banner severity="danger" title={title}>
        {message}
      </Banner>
      <Button type="button" size="sm" variant="secondary" icon={RefreshCw} loading={reloading} onClick={onReload}>
        {t("evidence.figureReload")}
      </Button>
    </div>
  );
}
