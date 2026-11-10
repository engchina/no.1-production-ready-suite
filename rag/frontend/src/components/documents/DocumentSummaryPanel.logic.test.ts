import { describe, expect, it } from "vitest";

import type { DocumentContentSummary } from "@/lib/api";

import { documentSummaryView, summaryFailureKey } from "./DocumentSummaryPanel.logic";

function summary(overrides: Partial<DocumentContentSummary>): DocumentContentSummary {
  return {
    status: "ready",
    generating: false,
    text: "合成の文書の要約です。",
    topics: ["合成の話題"],
    input_truncated: false,
    failure_code: null,
    generated_at: "2026-11-10T00:00:00Z",
    ...overrides,
  };
}

describe("documentSummaryView", () => {
  it("要約の行が無い文書は、まだ無い表示にする", () => {
    expect(documentSummaryView({ enabled: true, summary: null })).toEqual({ kind: "empty" });
  });

  it("作成済みの要約を出し、作り直しの途中と最後の失敗の理由を区別する", () => {
    expect(documentSummaryView({ enabled: true, summary: summary({}) })).toEqual({
      kind: "ready",
      regenerating: false,
      failure: null,
    });
    expect(documentSummaryView({ enabled: true, summary: summary({ generating: true }) })).toEqual({
      kind: "ready",
      regenerating: true,
      failure: null,
    });
    expect(
      documentSummaryView({ enabled: true, summary: summary({ failure_code: "timeout" }) })
    ).toEqual({ kind: "ready", regenerating: false, failure: "documents.summary.failure.timeout" });
  });

  it("最初の作成の途中は作成中、結果の無い失敗は理由つきの失敗にする", () => {
    const pending = summary({ status: "pending", generating: true, text: null, topics: [] });
    expect(documentSummaryView({ enabled: true, summary: pending })).toEqual({ kind: "generating" });
    const failed = summary({ status: "failed", text: null, topics: [], failure_code: "no_content" });
    expect(documentSummaryView({ enabled: true, summary: failed })).toEqual({
      kind: "failed",
      failure: "documents.summary.failure.no_content",
    });
    // 失敗の後の作り直しの途中は、失敗ではなく作成中を出す。
    expect(documentSummaryView({ enabled: true, summary: { ...failed, generating: true } })).toEqual({
      kind: "generating",
    });
  });

  it("知らない失敗の理由は一般の文言にする", () => {
    expect(summaryFailureKey("something_new")).toBe("documents.summary.failure.unknown");
    expect(summaryFailureKey(null)).toBe("documents.summary.failure.unknown");
    expect(summaryFailureKey("interrupted")).toBe("documents.summary.failure.interrupted");
  });
});
