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
  "trace.status.cancelled": "停止",
  "trace.status.stopped": "処理を停止しました",
  "trace.timing.reconstructed": "時刻の記録がない処理は、所要時間から順番を再構成しています。",
} as const;

export type TraceLabelKey = keyof typeof TRACE_DETAILS_JA;
export interface TraceDetailsLabels {
  segment: (index: number, total: number) => string;
  title: string; empty: string; partial: string; measured: string;
  notSampled: string;
  copy: string; copied: string; copyFailed: string; unknown: string;
  cancelled: string; stopped: string; reconstructed: string;
}
export function createTraceDetailsLabels(t: (key: TraceLabelKey) => string): TraceDetailsLabels {
  return {
    segment: (index, total) => t("trace.details.segment").replace("{index}", String(index)).replace("{total}", String(total)),    notSampled: t("trace.details.notSampled"),
    title: t("trace.details.title"), empty: t("trace.details.empty"), partial: t("trace.details.partial"), measured: t("trace.details.measured"),
    copy: t("trace.details.copy"), copied: t("trace.details.copied"), copyFailed: t("trace.details.copyFailed"), unknown: t("trace.details.unknown"),
    cancelled: t("trace.status.cancelled"), stopped: t("trace.status.stopped"), reconstructed: t("trace.timing.reconstructed"),
  };
}
export const DEFAULT_TRACE_DETAILS_LABELS = createTraceDetailsLabels(key => TRACE_DETAILS_JA[key]);
