import { describe, expect, it } from "vitest";

import {
  ingestConflictBannerIsStale,
  resolveIngestionParserDisplay,
  resolveIngestionProgressSummary,
  shouldShowProcessingWatchBanner,
} from "./DocumentWorkspace.logic";
import {
  resolveDocumentFailureView,
  resolveIngestionErrorDisplayPlan,
} from "./ingestion-error-display";

describe("resolveIngestionParserDisplay", () => {
  it("取込 segment の parser を source profile より優先して表示する", () => {
    const display = resolveIngestionParserDisplay({
      segments: [
        {
          status: "RUNNING",
          parser_backend: "mineru",
          parser_profile: "mineru",
        },
      ],
      extractionBackend: null,
      extractionProfile: null,
      loading: false,
    });

    expect(display).toEqual({
      backend: "mineru",
      profile: "mineru",
      source: "segment",
    });
  });

  it("segment がまだ無い取込中は初期判定 parser へ戻さず確認中にする", () => {
    const display = resolveIngestionParserDisplay({
      segments: [],
      extractionBackend: null,
      extractionProfile: null,
      loading: true,
    });

    expect(display).toEqual({
      backend: null,
      profile: null,
      source: "pending",
    });
  });

  it("保存済み extraction の parser 情報を segment 不在時の表示に使う", () => {
    const display = resolveIngestionParserDisplay({
      segments: [],
      extractionBackend: "docling",
      extractionProfile: "docling_adapter",
      loading: false,
    });

    expect(display).toEqual({
      backend: "docling",
      profile: "docling_adapter",
      source: "extraction",
    });
  });
});

describe("resolveIngestionProgressSummary", () => {
  it("page segment を完了数と失敗数へ集計する", () => {
    const summary = resolveIngestionProgressSummary([
      { status: "SUCCEEDED", progress_unit: "page", progress_start: 1, progress_end: 5 },
      { status: "SUCCEEDED", progress_unit: "page", progress_start: 6, progress_end: 9 },
      { status: "RUNNING", progress_unit: "page", progress_start: 10, progress_end: 13 },
      { status: "QUEUED", progress_unit: "page", progress_start: 14, progress_end: 17 },
    ]);

    expect(summary).toEqual({
      kind: "determinate",
      unit: "page",
      completed: 9,
      failed: 0,
      total: 17,
    });
  });

  it("source segment は indeterminate progress にする", () => {
    const summary = resolveIngestionProgressSummary([
      { status: "RUNNING", progress_unit: "source", progress_start: null, progress_end: null },
    ]);

    expect(summary).toEqual({ kind: "indeterminate" });
  });
});

describe("resolveIngestionErrorDisplayPlan", () => {
  it("job の原因を優先し、同じ原因の segment と document banner を隠す", () => {
    const plan = resolveIngestionErrorDisplayPlan({
      latestJobErrorMessage: " unlimited_ocr_adapter_failed ",
      segments: [
        {
          segment_id: "segment-1",
          status: "FAILED",
          error_message: "unlimited_ocr_adapter_failed",
        },
      ],
      documentErrorMessage: "unlimited_ocr_adapter_failed",
      queuedJobErrorMessage: "unlimited_ocr_adapter_failed",
      retriedSegmentJobErrorMessage: "unlimited_ocr_adapter_failed",
    });

    expect(plan.jobMessage).toBe("unlimited_ocr_adapter_failed");
    expect(Array.from(plan.segmentIds)).toEqual([]);
    expect(plan.documentMessage).toBeNull();
    expect(plan.queuedJobMessage).toBeNull();
    expect(plan.retriedSegmentJobMessage).toBeNull();
  });

  it("同じ segment 原因は最初の1件だけ表示する", () => {
    const plan = resolveIngestionErrorDisplayPlan({
      segments: [
        { segment_id: "segment-1", status: "FAILED", error_message: "parser failed" },
        { segment_id: "segment-2", status: "FAILED", error_message: " parser failed " },
        { segment_id: "segment-3", status: "FAILED", error_message: "ocr failed" },
      ],
    });

    expect(Array.from(plan.segmentIds)).toEqual(["segment-1", "segment-3"]);
  });

  it("segment と同じ document 原因は document banner に出さない", () => {
    const plan = resolveIngestionErrorDisplayPlan({
      segments: [
        { segment_id: "segment-1", status: "FAILED", error_message: "parser failed" },
      ],
      documentErrorMessage: " parser failed ",
    });

    expect(Array.from(plan.segmentIds)).toEqual(["segment-1"]);
    expect(plan.documentMessage).toBeNull();
  });

  it("異なる原因はそれぞれ表示する", () => {
    const plan = resolveIngestionErrorDisplayPlan({
      latestJobErrorMessage: "job failed",
      segments: [
        { segment_id: "segment-1", status: "FAILED", error_message: "segment failed" },
      ],
      documentErrorMessage: "document failed",
      queuedJobErrorMessage: "queued job failed",
      retriedSegmentJobErrorMessage: "retried job failed",
    });

    expect(plan.jobMessage).toBe("job failed");
    expect(Array.from(plan.segmentIds)).toEqual(["segment-1"]);
    expect(plan.documentMessage).toBe("document failed");
    expect(plan.queuedJobMessage).toBe("queued job failed");
    expect(plan.retriedSegmentJobMessage).toBe("retried job failed");
  });

  it("suppressMessages の原因は詳細側で再掲しない（§9 P2）", () => {
    const plan = resolveIngestionErrorDisplayPlan({
      segments: [
        { segment_id: "segment-1", status: "FAILED", error_message: " parser failed " },
        { segment_id: "segment-2", status: "FAILED", error_message: "ocr failed" },
      ],
      documentErrorMessage: "parser failed",
      queuedJobErrorMessage: "parser failed",
      suppressMessages: ["parser failed"],
    });

    // 上部バナーで出した "parser failed" は segment/document/queued から消える。
    expect(Array.from(plan.segmentIds)).toEqual(["segment-2"]);
    expect(plan.documentMessage).toBeNull();
    expect(plan.queuedJobMessage).toBeNull();
  });
});

describe("resolveDocumentFailureView", () => {
  it("ERROR でなければ errored=false で何も出さない", () => {
    const view = resolveDocumentFailureView({
      documentStatus: "INDEXED",
      segments: [],
    });
    expect(view).toEqual({ errored: false, failedStep: null, primaryMessage: null });
  });

  it("最新 FAILED ジョブの phase から失敗工程を導出し、原因を 1 本化する", () => {
    const view = resolveDocumentFailureView({
      documentStatus: "ERROR",
      latestJobStatus: "FAILED",
      latestJobPhase: "PREPROCESS",
      latestJobErrorMessage: " ファイル準備に失敗しました。再処理してください。 ",
      segments: [{ status: "FAILED", error_message: "別の原因" }],
      documentErrorMessage: "文書側の原因",
    });
    expect(view.errored).toBe(true);
    expect(view.failedStep).toBe("PREPROCESSING");
    // job → segment → document の順で最具体を採用（job を採る）。
    expect(view.primaryMessage).toBe("ファイル準備に失敗しました。再処理してください。");
  });

  it("ジョブ原因が無ければ失敗セグメント→文書の順にフォールバックする", () => {
    const view = resolveDocumentFailureView({
      documentStatus: "ERROR",
      latestJobStatus: "FAILED",
      latestJobPhase: "EXTRACT",
      latestJobErrorMessage: "   ",
      segments: [
        { status: "SUCCEEDED", error_message: null },
        { status: "FAILED", error_message: "segment 由来の原因" },
      ],
      documentErrorMessage: "文書側の原因",
    });
    expect(view.failedStep).toBe("INGESTING");
    expect(view.primaryMessage).toBe("segment 由来の原因");
  });

  it("ジョブが FAILED でなければ失敗工程は不明(null)", () => {
    const view = resolveDocumentFailureView({
      documentStatus: "ERROR",
      latestJobStatus: "SUCCEEDED",
      latestJobPhase: "INDEX",
      segments: [],
      documentErrorMessage: "文書側の原因",
    });
    expect(view.errored).toBe(true);
    expect(view.failedStep).toBeNull();
    expect(view.primaryMessage).toBe("文書側の原因");
  });
});

describe("shouldShowProcessingWatchBanner", () => {
  it("FAILED job があれば文書が一時的に INGESTING でも取込中 banner を出さない", () => {
    expect(
      shouldShowProcessingWatchBanner({
        watchProcessing: true,
        documentStatus: "INGESTING",
        latestJobStatus: "FAILED",
      })
    ).toBe(false);
  });

  it("ERROR は安定状態として取込中 banner を出さない", () => {
    expect(
      shouldShowProcessingWatchBanner({
        watchProcessing: true,
        documentStatus: "ERROR",
        latestJobStatus: "FAILED",
      })
    ).toBe(false);
  });
});

describe("ingestConflictBannerIsStale", () => {
  it("取込進行中の 409 は stale ではない(banner を残す)", () => {
    expect(ingestConflictBannerIsStale({ errorStatus: 409, hasActiveJob: true })).toBe(false);
  });

  it("取込完了後に残った 409 は stale として消す", () => {
    expect(ingestConflictBannerIsStale({ errorStatus: 409, hasActiveJob: false })).toBe(true);
  });

  it("409 以外のエラーは消さない", () => {
    expect(ingestConflictBannerIsStale({ errorStatus: 500, hasActiveJob: false })).toBe(false);
    expect(ingestConflictBannerIsStale({ errorStatus: null, hasActiveJob: false })).toBe(false);
  });
});
