import type { DocumentContentSummaryState } from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

/** 文書の要約の節に出す内容（#4793）。 */
export type DocumentSummaryView =
  /** まだ作っていない（機能が無効の間に取り込んだ文書・取込の前）。 */
  | { kind: "empty" }
  /** 最初の要約を作っている途中。 */
  | { kind: "generating" }
  /** 要約がある。`regenerating` は作り直しの途中、`failure` は最後の作り直しの失敗の理由。 */
  | { kind: "ready"; regenerating: boolean; failure: I18nKey | null }
  /** 要約を作れなかった。 */
  | { kind: "failed"; failure: I18nKey };

const FAILURE_KEYS: Record<string, I18nKey> = {
  timeout: "documents.summary.failure.timeout",
  model_failed: "documents.summary.failure.model_failed",
  invalid_output: "documents.summary.failure.invalid_output",
  no_content: "documents.summary.failure.no_content",
  source_missing: "documents.summary.failure.source_missing",
  index_failed: "documents.summary.failure.index_failed",
  interrupted: "documents.summary.failure.interrupted",
};

/** 失敗の理由の文言（知らない理由は一般の文言）。 */
export function summaryFailureKey(code: string | null): I18nKey {
  return (code && FAILURE_KEYS[code]) || "documents.summary.failure.unknown";
}

export function documentSummaryView(state: DocumentContentSummaryState): DocumentSummaryView {
  const summary = state.summary;
  if (!summary) return { kind: "empty" };
  if (summary.status === "ready") {
    return {
      kind: "ready",
      regenerating: summary.generating,
      failure: summary.failure_code ? summaryFailureKey(summary.failure_code) : null,
    };
  }
  if (summary.generating) return { kind: "generating" };
  if (summary.status === "failed") {
    return { kind: "failed", failure: summaryFailureKey(summary.failure_code) };
  }
  return { kind: "empty" };
}

/** 取込の job が動いている間と、終わってからこの時間は、後段の要約の状態を読み直す（ms）。 */
export const SUMMARY_AWAIT_AFTER_INGESTION_MS = 20_000;
