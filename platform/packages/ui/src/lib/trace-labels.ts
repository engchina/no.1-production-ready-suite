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
  "trace.details.legacy": "この実行は古い形式の記録のため、処理の経過を表示できません。",
  "trace.span.root": "実行の全体",
  "trace.span.chat": "モデルの呼び出し",
  "trace.span.embeddings": "埋め込みの作成",
  "trace.span.rerank": "並べ替え（rerank）",
  "trace.span.executeTool": "ツールの実行",
  "trace.span.genAi": "AI の呼び出し",
  "trace.span.mcpRequest": "MCP の呼び出し",
  "trace.span.selectAi": "Select AI の呼び出し",
  "trace.span.httpRequest": "HTTP の要求",
  "trace.span.attempt": "{name}（{count} 回目）",
  "trace.status.cancelled": "停止",
  "trace.status.stopped": "処理を停止しました",
  "trace.timing.reconstructed": "時刻の記録がない処理は、所要時間から順番を再構成しています。",
} as const;

export type TraceLabelKey = keyof typeof TRACE_DETAILS_JA;
export type TraceSpanStatus = "done" | "failed" | "cancelled" | "skipped";
/**
 * 製品の段（span の属性 `pr.stage`）の、処理の経過と同じ名前。処理の経過が段の行に使う関数をそのまま渡す。
 * 知らない段は返さない（部品が `pr.stage` の値を出す）。
 */
export type TraceStageName = (stage: string, status: TraceSpanStatus) => string | null | undefined;
/** 技術の span の名前（root と、`gen_ai.*`・MCP・Select AI・HTTP の呼び出し）。 */
export interface TraceSpanNames {
  root: string; chat: string; embeddings: string; rerank: string; executeTool: string; genAi: string;
  mcpRequest: string; selectAi: string; httpRequest: string;
  attempt: (name: string, count: number) => string;
}
export interface TraceDetailsLabels {
  segment: (index: number, total: number) => string;
  title: string; empty: string; partial: string; measured: string;
  notSampled: string; legacy: string;
  copy: string; copied: string; copyFailed: string; unknown: string;
  cancelled: string; stopped: string; reconstructed: string;
  spanNames: TraceSpanNames;
  stageName?: TraceStageName;
}
export function createTraceDetailsLabels(t: (key: TraceLabelKey) => string, options: { stageName?: TraceStageName } = {}): TraceDetailsLabels {
  return {
    segment: (index, total) => t("trace.details.segment").replace("{index}", String(index)).replace("{total}", String(total)),
    notSampled: t("trace.details.notSampled"), legacy: t("trace.details.legacy"),
    title: t("trace.details.title"), empty: t("trace.details.empty"), partial: t("trace.details.partial"), measured: t("trace.details.measured"),
    copy: t("trace.details.copy"), copied: t("trace.details.copied"), copyFailed: t("trace.details.copyFailed"), unknown: t("trace.details.unknown"),
    cancelled: t("trace.status.cancelled"), stopped: t("trace.status.stopped"), reconstructed: t("trace.timing.reconstructed"),
    spanNames: {
      root: t("trace.span.root"), chat: t("trace.span.chat"), embeddings: t("trace.span.embeddings"), rerank: t("trace.span.rerank"),
      executeTool: t("trace.span.executeTool"), genAi: t("trace.span.genAi"), mcpRequest: t("trace.span.mcpRequest"),
      selectAi: t("trace.span.selectAi"), httpRequest: t("trace.span.httpRequest"),
      attempt: (name, count) => t("trace.span.attempt").replace("{name}", name).replace("{count}", String(count)),
    },
    stageName: options.stageName,
  };
}
export const DEFAULT_TRACE_DETAILS_LABELS = createTraceDetailsLabels(key => TRACE_DETAILS_JA[key]);
