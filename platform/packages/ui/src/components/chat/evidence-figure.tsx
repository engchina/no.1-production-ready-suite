import { ImageIcon, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiErrorMessage } from "../../lib/api-error";
import { cn } from "../../lib/utils";
import { TimedLoadingState } from "../feedback/processing-state";
import { Banner } from "../ui/banner";
import { Button } from "../ui/button";
import { SideSheet } from "../ui/side-sheet";
import { Skeleton } from "../ui/skeleton";

/**
 * 図の根拠の「図を開く」（RAG のチャット・RAG 検索・実行の詳細、Agent のチャット・実行の詳細で共通。#1311・#2428）。
 *
 * 根拠のカードの操作の行と、根拠のサイドパネルの要約の操作の行に置く。図の根拠（元の図の領域が分かる根拠）にだけ
 * 製品が置く。押すと右のサイドパネルを開き、製品が渡す {@link EvidenceFigureActionProps.load} で図の画像の URL を取り、
 * その画像を出す。URL は開くたびに取り直し（短命の URL・今の権限と版で確かめるため）、閉じると捨てる。
 *
 * データの取り方は製品が持つ: Agent は backend が RAG に作らせる短命の署名つき URL、RAG は画面用の API の画像。
 */

/** 文言（すべて翻訳済み）。既定は {@link evidenceFigureLabels}（3 製品で同じ）。 */
export interface EvidenceFigureLabels {
  open: string;
  /** ボタンの読み上げ（例:「図を開く: 文書 A.pdf」）。 */
  openLabel: (title: string) => string;
  /** サイドパネルの見出し（例:「図の根拠: 文書 A.pdf」）。 */
  title: (title: string) => string;
  close: string;
  loading: string;
  /** 画像の代替テキスト（`location` は頁・節。空なら文書名だけ）。 */
  alt: (title: string, location: string | null) => string;
  failed: string;
  /** URL・画像を取れなかったときの既定の理由（製品の API の理由が無いとき）。 */
  failedFallback: string;
  /** 取った URL の画像を読めなかったとき。 */
  imageFailed: string;
  reload: string;
  /** AI が図を読んだ説明の根拠に添える注記。 */
  aiDescription: string;
  /** パネルの画像の上の説明。 */
  note: string;
}

/** 3 製品で同じ「図を開く」の文言。 */
export function evidenceFigureLabels(overrides: Partial<EvidenceFigureLabels> = {}): EvidenceFigureLabels {
  return {
    open: "図を開く",
    openLabel: (title) => `図を開く: ${title}`,
    title: (title) => `図の根拠: ${title}`,
    close: "図を閉じる",
    loading: "図を読み込んでいます",
    alt: (title, location) => (location ? `${title} の図（${location}）` : `${title} の図`),
    failed: "図を開けませんでした",
    failedFallback: "図を取得できませんでした。時間をおいて再度お試しください。",
    imageFailed: "図の画像を読み込めませんでした。開く期限が切れたか、資料が更新された可能性があります。もう一度読み込んでください。",
    reload: "もう一度読み込む",
    aiDescription: "AI が図を読んだ説明です。元の図で確かめてください。",
    note: "元の文書の図の領域です。表示のたびに今の権限と資料の版で確かめます。",
    ...overrides,
  };
}

export const DEFAULT_EVIDENCE_FIGURE_LABELS: EvidenceFigureLabels = evidenceFigureLabels();

/** 製品が返す図の画像。`release` は閉じた・取り直したときに呼ぶ（`URL.createObjectURL` の解放など）。 */
export interface EvidenceFigureImage {
  url: string;
  release?: () => void;
}

export interface EvidenceFigureActionProps {
  /** 根拠の文書名（ボタンの読み上げ・見出し・代替テキスト）。 */
  title: string;
  /** 根拠の場所（頁・節）。代替テキストに使う。 */
  location: string | null;
  /** AI（VLM）が図を読んだ説明の根拠か（注記を添える）。 */
  aiDescription?: boolean;
  /** 図の画像の URL を取る（開くたび・取り直しのたびに呼ぶ。閉じたら `signal` を中断する）。失敗は理由の Error で投げる。 */
  load: (signal: AbortSignal) => Promise<EvidenceFigureImage>;
  labels?: EvidenceFigureLabels;
  /** ボタンの test id（サイドパネルは `<testId>-sheet`）。 */
  testId?: string;
}

/** 図の根拠の操作（AI が読んだ説明ならその旨と、元の図を開くボタン・サイドパネル）。 */
export function EvidenceFigureAction({
  title,
  location,
  aiDescription = false,
  load,
  labels = DEFAULT_EVIDENCE_FIGURE_LABELS,
  testId,
}: EvidenceFigureActionProps) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {aiDescription ? (
        <p className="text-xs text-fg-muted" data-testid="evidence-figure-description">
          {labels.aiDescription}
        </p>
      ) : null}
      <Button
        type="button"
        size="sm"
        variant="secondary"
        icon={ImageIcon}
        aria-haspopup="dialog"
        aria-label={labels.openLabel(title)}
        onClick={() => setOpen(true)}
        data-testid={testId}
      >
        {labels.open}
      </Button>
      <SideSheet
        open={open}
        onClose={() => setOpen(false)}
        title={labels.title(title)}
        closeLabel={labels.close}
        side="right"
        size="wide"
        data-testid={testId ? `${testId}-sheet` : undefined}
      >
        {/* 閉じている間は描かない（開くたびに URL を取り直す）。 */}
        {open ? <FigureViewer title={title} location={location} load={load} labels={labels} /> : null}
      </SideSheet>
    </div>
  );
}

type FigureState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; url: string; image: "loading" | "loaded" | "error" };

function FigureViewer({
  title,
  location,
  load,
  labels,
}: {
  title: string;
  location: string | null;
  load: EvidenceFigureActionProps["load"];
  labels: EvidenceFigureLabels;
}) {
  const [state, setState] = useState<FigureState>({ status: "loading" });
  // 取り直しの回数（同じ URL が返っても img を作り直して読み込み直す）。
  const [attempt, setAttempt] = useState(0);
  // `load` は製品の描画ごとに作り直されるので、取り直しは attempt だけで起こす（最新の `load` を使う）。
  const loader = useRef(load);
  useEffect(() => {
    loader.current = load;
  });

  useEffect(() => {
    const controller = new AbortController();
    let release: (() => void) | undefined;
    setState({ status: "loading" });
    loader
      .current(controller.signal)
      .then((image) => {
        if (controller.signal.aborted) {
          image.release?.();
          return;
        }
        release = image.release;
        setState({ status: "ready", url: image.url, image: "loading" });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", message: apiErrorMessage(error, labels.failedFallback) });
      });
    return () => {
      controller.abort();
      release?.();
    };
  }, [attempt, labels.failedFallback]);

  const reload = () => setAttempt((value) => value + 1);
  if (state.status === "error") {
    return <FigureFailure title={labels.failed} message={state.message} onReload={reload} labels={labels} />;
  }
  const url = state.status === "ready" ? state.url : null;
  const image = state.status === "ready" ? state.image : "loading";
  const setImage = (next: "loaded" | "error") =>
    setState((current) => (current.status === "ready" && current.url === url ? { ...current, image: next } : current));
  return (
    <div className="space-y-3" data-testid="evidence-figure-viewer">
      <p className="text-xs text-fg-muted">{labels.note}</p>
      {image === "error" ? (
        <FigureFailure title={labels.failed} message={labels.imageFailed} onReload={reload} labels={labels} />
      ) : null}
      {image === "loading" ? (
        <TimedLoadingState label={labels.loading} testId="evidence-figure-loading">
          <Skeleton className="h-64 w-full" />
        </TimedLoadingState>
      ) : null}
      {url && image !== "error" ? (
        <img
          key={`${url}#${attempt}`}
          src={url}
          alt={labels.alt(title, location)}
          // 署名つきの URL（トークン）を Referer で他へ渡さない。
          referrerPolicy="no-referrer"
          decoding="async"
          className={cn(
            "block h-auto max-w-full rounded-sm border border-border bg-surface",
            image === "loaded" ? null : "sr-only"
          )}
          onLoad={() => setImage("loaded")}
          onError={() => setImage("error")}
          data-testid="evidence-figure-image"
        />
      ) : null}
    </div>
  );
}

function FigureFailure({
  title,
  message,
  onReload,
  labels,
}: {
  title: string;
  message: string;
  onReload: () => void;
  labels: EvidenceFigureLabels;
}) {
  return (
    <div className="space-y-2" data-testid="evidence-figure-error">
      <Banner severity="danger" title={title}>
        {message}
      </Banner>
      <Button type="button" size="sm" variant="secondary" icon={RefreshCw} onClick={onReload}>
        {labels.reload}
      </Button>
    </div>
  );
}
