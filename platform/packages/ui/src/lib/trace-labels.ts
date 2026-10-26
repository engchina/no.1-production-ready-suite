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
  "trace.details.notReported": "不明",
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
  "trace.field.usage.input": "入力トークン",
  "trace.field.usage.output": "出力トークン",
  "trace.field.usage.cacheRead": "キャッシュから読んだ入力",
  "trace.field.usage.reasoning": "推論の出力トークン",
  "trace.field.estimate.staticInstructions": "入力の見積もり（指示・固定）",
  "trace.field.estimate.dynamicState": "入力の見積もり（動的な状態）",
  "trace.field.estimate.toolDefinitions": "入力の見積もり（ツールの定義）",
  "trace.field.estimate.history": "入力の見積もり（履歴）",
  "trace.field.estimate.toolOutputs": "入力の見積もり（ツールの結果）",
  "trace.field.estimate.input": "入力の見積もり（合計）",
  "trace.field.input.items": "入力の項目数",
  "trace.field.budget.input": "文脈の予算",
  "trace.field.budget.remaining": "文脈の予算の残り",
  "trace.field.compaction.before": "圧縮の前",
  "trace.field.compaction.after": "圧縮の後",
  "trace.field.compaction.outputs": "圧縮したツールの結果の数",
  "trace.field.instructions.staticFingerprint": "指示の指紋（固定）",
  "trace.field.instructions.fingerprint": "指示の指紋（全体）",
  "trace.field.tools.fingerprint": "ツール定義の指紋",
  "trace.field.tools.count": "ツール定義の数",
  "trace.status.cancelled": "停止",
  "trace.status.stopped": "処理を停止しました",
  "trace.timing.reconstructed": "時刻の記録がない処理は、所要時間から順番を再構成しています。",
  "trace.product.rag": "RAG",
  "trace.product.nl2sql": "NL2SQL",
  "trace.product.agent": "Agent",
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
/**
 * span の詳細の、利用量（`gen_ai.usage.*`）と入力の文脈の記録（`pr.context.*`。#2583）の行の見出し（属性名 → 文言のキー）。
 * 並びは詳細の行の順（利用量 → 入力の見積もり → 予算 → 圧縮 → 指紋）。識別子の行（`pr.stage`・モデル・ツール など）は
 * 属性名のまま出す。
 */
export const TRACE_FIELD_LABEL_KEYS = {
  "gen_ai.usage.input_tokens": "trace.field.usage.input",
  "gen_ai.usage.output_tokens": "trace.field.usage.output",
  "gen_ai.usage.cache_read.input_tokens": "trace.field.usage.cacheRead",
  "gen_ai.usage.reasoning.output_tokens": "trace.field.usage.reasoning",
  "pr.context.estimate.static_instructions_tokens": "trace.field.estimate.staticInstructions",
  "pr.context.estimate.dynamic_state_tokens": "trace.field.estimate.dynamicState",
  "pr.context.estimate.tool_definitions_tokens": "trace.field.estimate.toolDefinitions",
  "pr.context.estimate.history_tokens": "trace.field.estimate.history",
  "pr.context.estimate.tool_outputs_tokens": "trace.field.estimate.toolOutputs",
  "pr.context.estimate.input_tokens": "trace.field.estimate.input",
  "pr.context.input.items": "trace.field.input.items",
  "pr.context.budget.input_tokens": "trace.field.budget.input",
  "pr.context.budget.remaining_tokens": "trace.field.budget.remaining",
  "pr.context.compaction.tokens_before": "trace.field.compaction.before",
  "pr.context.compaction.tokens_after": "trace.field.compaction.after",
  "pr.context.compaction.outputs": "trace.field.compaction.outputs",
  "pr.context.instructions.static_fingerprint": "trace.field.instructions.staticFingerprint",
  "pr.context.instructions.fingerprint": "trace.field.instructions.fingerprint",
  "pr.context.tools.fingerprint": "trace.field.tools.fingerprint",
  "pr.context.tools.count": "trace.field.tools.count",
} as const satisfies Readonly<Record<string, TraceLabelKey>>;
export type TraceFieldName = keyof typeof TRACE_FIELD_LABEL_KEYS;

export interface TraceDetailsLabels {
  segment: (index: number, total: number) => string;
  title: string; empty: string; partial: string; measured: string;
  notSampled: string; legacy: string;
  copy: string; copied: string; copyFailed: string; unknown: string;
  /** プロバイダが返さなかった値（`gen_ai.usage.cache_read.input_tokens` が無い。0 と区別する）。 */
  notReported: string;
  /** 利用量と入力の文脈の記録の行の見出し（`TRACE_FIELD_LABEL_KEYS`）。 */
  fieldNames: Readonly<Record<TraceFieldName, string>>;
  cancelled: string; stopped: string; reconstructed: string;
  spanNames: TraceSpanNames;
  stageName?: TraceStageName;
  /**
   * 画面の製品（`rag` / `nl2sql` / `agent`。#3272）。別の製品が書いた span は、この製品の工程の定義で訳さずに、書いた製品が
   * 付けた名前（`display_name`）を出し、製品の境目の行に製品名を添える。
   */
  product?: string;
  /** span を書いた製品の名前（知らない値はそのまま）。 */
  productName: (product: string) => string;
}
const PRODUCT_KEYS: Readonly<Record<string, TraceLabelKey>> = { rag: "trace.product.rag", nl2sql: "trace.product.nl2sql", agent: "trace.product.agent" };
export function createTraceDetailsLabels(t: (key: TraceLabelKey) => string, options: { stageName?: TraceStageName; product?: string } = {}): TraceDetailsLabels {
  return {
    segment: (index, total) => t("trace.details.segment").replace("{index}", String(index)).replace("{total}", String(total)),
    notSampled: t("trace.details.notSampled"), legacy: t("trace.details.legacy"),
    title: t("trace.details.title"), empty: t("trace.details.empty"), partial: t("trace.details.partial"), measured: t("trace.details.measured"),
    copy: t("trace.details.copy"), copied: t("trace.details.copied"), copyFailed: t("trace.details.copyFailed"), unknown: t("trace.details.unknown"),
    notReported: t("trace.details.notReported"),
    fieldNames: Object.fromEntries(Object.entries(TRACE_FIELD_LABEL_KEYS).map(([name, key]) => [name, t(key)])) as Record<TraceFieldName, string>,
    cancelled: t("trace.status.cancelled"), stopped: t("trace.status.stopped"), reconstructed: t("trace.timing.reconstructed"),
    spanNames: {
      root: t("trace.span.root"), chat: t("trace.span.chat"), embeddings: t("trace.span.embeddings"), rerank: t("trace.span.rerank"),
      executeTool: t("trace.span.executeTool"), genAi: t("trace.span.genAi"), mcpRequest: t("trace.span.mcpRequest"),
      selectAi: t("trace.span.selectAi"), httpRequest: t("trace.span.httpRequest"),
      attempt: (name, count) => t("trace.span.attempt").replace("{name}", name).replace("{count}", String(count)),
    },
    stageName: options.stageName,
    product: options.product,
    productName: (product) => (Object.prototype.hasOwnProperty.call(PRODUCT_KEYS, product) ? t(PRODUCT_KEYS[product]) : product),
  };
}
export const DEFAULT_TRACE_DETAILS_LABELS = createTraceDetailsLabels(key => TRACE_DETAILS_JA[key]);
