/** trace の共通文言の正本。製品の i18n へ同じ辞書を取り込む。 */
export const TRACE_DETAILS_JA = {
  "trace.details.segment": "実行区間 {index}/{total}",
  "trace.details.title": "トレースの詳細",
  "trace.details.empty": "実測の記録はありません。",
  "trace.details.partial": "一部の処理だけを記録しています。",
  "trace.details.measured": "実測した処理の親子関係と所要時間です。",
  "trace.details.copy": "Trace ID をコピー",
  "trace.details.copied": "Trace ID をコピーしました。",
  "trace.details.copyFailed": "Trace ID をコピーできませんでした。",
  "trace.details.notSampled": "外部送信の抽出対象ではありません。画面用の記録は保持しています。",
  "trace.details.unknown": "未計測",
  "trace.span.root": "実行の全体",
  "trace.status.cancelled": "停止",
  "trace.status.stopped": "処理を停止しました",
  "trace.timing.reconstructed": "時刻の記録がない処理は、所要時間から順番を再構成しています。",
} as const;

export type TraceLabelKey = keyof typeof TRACE_DETAILS_JA;
/** span の表示名を決める入力。属性は backend の allowlist を通った値だけ。 */
export interface TraceSpanLabelInput {
  operationName: string;
  /** 属性 `pr.stage`（製品の段・工程の名前）。 */
  stage?: string;
  status: "done" | "failed" | "cancelled" | "skipped";
  /** 実行の全体の span（`root_span_id`）。 */
  root: boolean;
  attributes: Readonly<Record<string, unknown>>;
}
/**
 * 製品が処理の経過と同じ名前を span に付ける関数。返さなければ部品の既定の名前
 * （root は「実行の全体」、`gen_ai.*` は技術の名前とモデル名・ツール名、それ以外は `operation_name`）にする。
 */
export type TraceSpanLabel = (span: TraceSpanLabelInput) => string | null | undefined;
export interface TraceDetailsLabels {
  segment: (index: number, total: number) => string;
  title: string; empty: string; partial: string; measured: string;
  notSampled: string;
  copy: string; copied: string; copyFailed: string; unknown: string;
  cancelled: string; stopped: string; reconstructed: string;
  root: string;
  spanLabel?: TraceSpanLabel;
}
export function createTraceDetailsLabels(t: (key: TraceLabelKey) => string, options: { spanLabel?: TraceSpanLabel } = {}): TraceDetailsLabels {
  return {
    segment: (index, total) => t("trace.details.segment").replace("{index}", String(index)).replace("{total}", String(total)),
    notSampled: t("trace.details.notSampled"),
    title: t("trace.details.title"), empty: t("trace.details.empty"), partial: t("trace.details.partial"), measured: t("trace.details.measured"),
    copy: t("trace.details.copy"), copied: t("trace.details.copied"), copyFailed: t("trace.details.copyFailed"), unknown: t("trace.details.unknown"),
    cancelled: t("trace.status.cancelled"), stopped: t("trace.status.stopped"), reconstructed: t("trace.timing.reconstructed"),
    root: t("trace.span.root"), spanLabel: options.spanLabel,
  };
}
export const DEFAULT_TRACE_DETAILS_LABELS = createTraceDetailsLabels(key => TRACE_DETAILS_JA[key]);
