/** trace の共通文言の正本。製品の i18n へ同じ辞書を取り込む。 */
export const TRACE_DETAILS_JA = {
  "trace.details.conversationId": "会話 ID",
  "trace.details.messageId": "回答メッセージ ID",
  "trace.details.models": "使用したモデル",
  "trace.details.copyIdentifier": "{label} をコピー",
  "trace.details.identifierCopied": "{label} をコピーしました。",
  "trace.details.identifierCopyFailed": "{label} をコピーできませんでした。",
  "trace.details.segment": "実行区間 {index}/{total}",
  "trace.details.title": "トレースの詳細",
  "trace.details.empty": "実測の記録はありません。",
  "trace.details.partial": "一部の処理だけを記録しています。",
  "trace.details.readTruncated": "取得件数の上限に達したため、一部の処理だけを表示しています。保存済みの記録がすべて表示されているとは限りません。",
  "trace.details.measured": "実測した処理の親子関係と所要時間です。",
  "trace.details.copy": "Trace ID をコピー",
  "trace.details.copied": "Trace ID をコピーしました。",
  "trace.details.copyFailed": "Trace ID をコピーできませんでした。",
  "trace.details.notSampled": "外部送信の抽出対象ではありません。画面用の記録は保持しています。",
  "trace.details.unknown": "未計測",
  "trace.details.notReported": "不明",
  "trace.details.legacy": "この実行は古い形式の記録のため、処理の経過を表示できません。",
  "trace.field.batchIndex": "バッチ番号",
  "trace.field.batchCount": "今回のバッチ数",
  "trace.field.batchPass": "読取の巡回",
  "trace.field.retryReason": "再試行の理由",
  "trace.field.transportStatus": "モデルの応答",
  "trace.field.validationStatus": "結果の検証",
  "trace.value.returned": "応答を受信",
  "trace.value.failed": "通信に失敗",
  "trace.value.rejected": "検証で不合格",
  "trace.value.accepted": "検証に合格",
  "trace.value.incomplete": "応答が未完了",
  "trace.value.citation_range_invalid": "引用範囲が不正",
  "trace.value.invalid_structured_output": "返却形式が不正",
  "trace.value.transport_retry": "通信の再試行",
  "trace.value.runaway_output": "出力の反復",
  "trace.value.stream_failed": "受信の失敗",
  "trace.value.stream_stalled": "受信の停滞",
  "trace.value.incomplete_output": "出力が未完了",

  "trace.purpose.guide.draft": "回答の過程からガイドの下書きを作る",
  "trace.purpose.answer.validate": "回答の段落を取得した資料と照合する",
  "trace.purpose.answer.recheck": "未確認の段落を根拠と照合し直す",
  "trace.purpose.answer.premise_consistency": "確認済みの利用者条件と回答を照合する",
  "trace.purpose.sql.structure": "SQL から表と列の構造を取り出す",
  "trace.purpose.sql.logical_structure": "SQL の処理を論理構造に整理する",
  "trace.purpose.answer.generate": "根拠から回答文を作る",
  "trace.purpose.answer.audit": "回答の主張を根拠と照合する",
  "trace.purpose.answer.audit_reason": "導いた結論の判定の理由を書く",
  "trace.purpose.answer.repair": "根拠に合わせて回答を直す",
  "trace.purpose.answer.resolve_conflicts": "表の値の食い違いを照合する",
  "trace.purpose.answer.row_condition_type": "表の各行の条件の種類と時点を読み取る",
  "trace.purpose.answer.row_condition_judge": "表の各行の条件が質問から成り立つか判定する",
  "trace.purpose.evidence.read_batch": "根拠を分割して読む",
  "trace.purpose.evidence.repair_batch_range": "読取結果の引用範囲を修正する（再試行）",
  "trace.purpose.evidence.select_screen": "質問に関係する画面を選ぶ",
  "trace.purpose.query.route": "質問に合う検索方式と検索文を選ぶ",
  "trace.purpose.query.rewrite": "前の会話を踏まえて質問を書き換える",
  "trace.purpose.query.conditions": "質問から絞り込み条件を読み取る",
  "trace.purpose.query.subjects": "質問から対象の名前を取り出す",
  "trace.purpose.query.subject_roles": "質問の対象の名前の役割を判定する",
  "trace.purpose.query.embed": "検索する質問の埋め込みを作る",
  "trace.purpose.document.embed": "検索に使う文書の埋め込みを作る",
  "trace.purpose.sql.generate": "質問に答える SQL を作る",
  "trace.purpose.sql.repair": "エラーになった SQL を直す",
  "trace.purpose.sql.analyze": "SQL の失敗原因と直し方を示す",
  "trace.purpose.sql.describe": "SQL が行う処理を説明する",
  "trace.purpose.sql.reverse": "SQL の処理を表す質問を作る",
  "trace.purpose.schema.comments": "表と列の説明を作る",
  "trace.purpose.schema.relations": "SQL から表の関係と条件を取り出す",
  "trace.purpose.ontology.build": "資料から対象の構造と関係を取り出す",
  "trace.purpose.ontology.review": "構造と関係の定義を検査する",
  "trace.purpose.requirement.clarify": "回答前に必要な確認事項を作る",
  "trace.purpose.evaluation.judge": "回答を評価基準と照合する",
  "trace.purpose.document.read": "文書の画像から内容を読み取る",
  "trace.purpose.document.extract": "文書から定義した項目を取り出す",
  "trace.purpose.document.summarize": "文書の範囲ごとに要点をまとめる",
  "trace.purpose.document.infer_headings": "見出しの親子関係を推定する",
  "trace.purpose.context.compact": "次の処理に必要な履歴を要約する",
  "trace.purpose.agent.choose_action": "質問に応じた操作または回答を作る",
  "trace.purpose.connection.check": "モデルが応答するか接続を確かめる",
  "trace.field.modelPurpose": "モデルの呼び出しの用途",
  "trace.span.root": "実行の全体",
  "trace.span.chat": "モデルを呼び出す（用途の記録なし）",
  "trace.span.embeddings": "テキストの埋め込みを作る",
  "trace.span.rerank": "質問との関連度で候補を並べ替える",
  "trace.span.rerankRequest": "候補の並べ替えを要求する",
  "trace.span.executeTool": "ツールの実行",
  "trace.span.genAi": "AI の呼び出し",
  "trace.span.mcpRequest": "MCP の呼び出し",
  "trace.span.selectAi": "Select AI の呼び出し",
  "trace.span.attachments": "会話の添付から根拠を選ぶ",
  "trace.span.other": "処理を実行する",
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
  "trace.timing.reconstructed": "開始時刻が未記録の処理は、時間の線に表示していません。",
  "trace.product.rag": "RAG",
  "trace.product.nl2sql": "NL2SQL",
  "trace.product.agent": "Agent",
} as const;

const MODEL_PURPOSE_LABEL_KEYS = {
  "guide.draft": "trace.purpose.guide.draft",
  "answer.validate": "trace.purpose.answer.validate",
  "answer.recheck": "trace.purpose.answer.recheck",
  "answer.premise_consistency": "trace.purpose.answer.premise_consistency",
  "sql.structure": "trace.purpose.sql.structure",
  "sql.logical_structure": "trace.purpose.sql.logical_structure",
  "answer.generate": "trace.purpose.answer.generate",
  "answer.audit": "trace.purpose.answer.audit",
  "answer.audit_reason": "trace.purpose.answer.audit_reason",
  "answer.repair": "trace.purpose.answer.repair",
  "answer.resolve_conflicts": "trace.purpose.answer.resolve_conflicts",
  "answer.row_condition_type": "trace.purpose.answer.row_condition_type",
  "answer.row_condition_judge": "trace.purpose.answer.row_condition_judge",
  "evidence.read_batch": "trace.purpose.evidence.read_batch",
  "evidence.repair_batch_range": "trace.purpose.evidence.repair_batch_range",
  "evidence.select_screen": "trace.purpose.evidence.select_screen",
  "query.route": "trace.purpose.query.route",
  "query.rewrite": "trace.purpose.query.rewrite",
  "query.conditions": "trace.purpose.query.conditions",
  "query.subjects": "trace.purpose.query.subjects",
  "query.subject_roles": "trace.purpose.query.subject_roles",
  "query.embed": "trace.purpose.query.embed",
  "document.embed": "trace.purpose.document.embed",
  "sql.generate": "trace.purpose.sql.generate",
  "sql.repair": "trace.purpose.sql.repair",
  "sql.analyze": "trace.purpose.sql.analyze",
  "sql.describe": "trace.purpose.sql.describe",
  "sql.reverse": "trace.purpose.sql.reverse",
  "schema.comments": "trace.purpose.schema.comments",
  "schema.relations": "trace.purpose.schema.relations",
  "ontology.build": "trace.purpose.ontology.build",
  "ontology.review": "trace.purpose.ontology.review",
  "requirement.clarify": "trace.purpose.requirement.clarify",
  "evaluation.judge": "trace.purpose.evaluation.judge",
  "document.read": "trace.purpose.document.read",
  "document.extract": "trace.purpose.document.extract",
  "document.summarize": "trace.purpose.document.summarize",
  "document.infer_headings": "trace.purpose.document.infer_headings",
  "context.compact": "trace.purpose.context.compact",
  "agent.choose_action": "trace.purpose.agent.choose_action",
  "connection.check": "trace.purpose.connection.check",
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
  attachments: string; other: string; root: string; chat: string; embeddings: string; rerank: string; rerankRequest: string; executeTool: string; genAi: string;
  mcpRequest: string; selectAi: string; httpRequest: string;
  attempt: (name: string, count: number) => string;
}
/**
 * span の詳細の、利用量（`gen_ai.usage.*`）と入力の文脈の記録（`pr.context.*`。#2583）の行の見出し（属性名 → 文言のキー）。
 * 並びは詳細の行の順（利用量 → 入力の見積もり → 予算 → 圧縮 → 指紋）。識別子の行（`pr.stage`・モデル・ツール など）は
 * 属性名のまま出す。
 */
export const TRACE_FIELD_LABEL_KEYS = {
  "pr.gen_ai.purpose": "trace.field.modelPurpose",
  "pr.batch.index": "trace.field.batchIndex",
  "pr.batch.count": "trace.field.batchCount",
  "pr.batch.pass": "trace.field.batchPass",
  "pr.retry.reason": "trace.field.retryReason",
  "pr.call.transport_status": "trace.field.transportStatus",
  "pr.call.validation_status": "trace.field.validationStatus",
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
  processMetadata?: { conversationId: string; messageId: string; models: string; copy: string; copied: string; copyFailed: string };
  segment: (index: number, total: number) => string;
  title: string; empty: string; partial: string; readTruncated?: string; measured: string;
  notSampled: string; legacy: string;
  copy: string; copied: string; copyFailed: string; unknown: string;
  /** プロバイダが返さなかった値（`gen_ai.usage.cache_read.input_tokens` が無い。0 と区別する）。 */
  notReported: string;
  /** 利用量と入力の文脈の記録の行の見出し（`TRACE_FIELD_LABEL_KEYS`）。 */
  fieldNames: Readonly<Record<TraceFieldName, string>>;
  valueNames?: Readonly<Record<string, string>>;
  cancelled: string; stopped: string; reconstructed: string;
  spanNames: TraceSpanNames;
  modelPurposes?: Readonly<Record<string, string>>;
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
    processMetadata: { conversationId: t("trace.details.conversationId"), messageId: t("trace.details.messageId"), models: t("trace.details.models"), copy: t("trace.details.copyIdentifier"), copied: t("trace.details.identifierCopied"), copyFailed: t("trace.details.identifierCopyFailed") },
    segment: (index, total) => t("trace.details.segment").replace("{index}", String(index)).replace("{total}", String(total)),
    notSampled: t("trace.details.notSampled"), legacy: t("trace.details.legacy"),
    title: t("trace.details.title"), empty: t("trace.details.empty"), partial: t("trace.details.partial"), readTruncated: t("trace.details.readTruncated"), measured: t("trace.details.measured"),
    copy: t("trace.details.copy"), copied: t("trace.details.copied"), copyFailed: t("trace.details.copyFailed"), unknown: t("trace.details.unknown"),
    notReported: t("trace.details.notReported"),
    valueNames: Object.fromEntries(["returned", "failed", "rejected", "accepted", "incomplete", "citation_range_invalid", "invalid_structured_output", "transport_retry", "runaway_output", "stream_failed", "stream_stalled", "incomplete_output"].map(value => [value, t(`trace.value.${value}` as TraceLabelKey)])),
    fieldNames: Object.fromEntries(Object.entries(TRACE_FIELD_LABEL_KEYS).map(([name, key]) => [name, t(key)])) as Record<TraceFieldName, string>,
    cancelled: t("trace.status.cancelled"), stopped: t("trace.status.stopped"), reconstructed: t("trace.timing.reconstructed"),
    modelPurposes: Object.fromEntries(Object.entries(MODEL_PURPOSE_LABEL_KEYS).map(([id, key]) => [id, t(key)])),
    spanNames: {
      attachments: t("trace.span.attachments"), other: t("trace.span.other"), root: t("trace.span.root"), chat: t("trace.span.chat"), embeddings: t("trace.span.embeddings"), rerank: t("trace.span.rerank"), rerankRequest: t("trace.span.rerankRequest"),
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
