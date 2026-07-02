import { describe, expect, it } from "vitest";

import {
  ingestConflictBannerIsStale,
  phaseLabelKey,
  phaseRetryLabelKey,
  phaseRunningMessageKey,
  phaseStartedMessageKey,
  resolveDocumentActionPlan,
  resolveIngestionParserDisplay,
  resolveIngestionProgressSummary,
  resolveLatestJobsByPhase,
  shouldShowProcessingWatchBanner,
} from "./DocumentWorkspace.logic";
import {
  resolveDocumentFailureView,
  resolveIngestionErrorDisplayPlan,
} from "./ingestion-error-display";
import { t } from "@/lib/i18n";
import type { FileStatus, IngestionJobPhase } from "@/lib/api";

describe("文書処理の表示名", () => {
  it.each<[FileStatus, string]>([
    ["UPLOADED", "ファイル準備待ち"],
    ["PREPROCESSING", "ファイル準備中"],
    ["PREPROCESSED", "ファイル準備確認待ち"],
    ["INGESTING", "解析（抽出）中"],
    ["REVIEW", "抽出確認待ち"],
    ["CHUNKING", "Chunk 作成中"],
    ["CHUNKED", "Chunk 確認待ち"],
    ["INDEXING", "Embedding / 索引中"],
    ["INDEXED", "索引済み"],
    ["ERROR", "エラー"],
  ])("%s を %s と表示する", (status, label) => {
    expect(t(`status.${status}`)).toBe(label);
  });

  it.each<[IngestionJobPhase, string, string, string, string]>([
    [
      "PREPROCESS",
      "ファイル準備",
      "ファイル準備を開始しました。完了まで状態を更新します。",
      "ファイル準備を実行しています。完了まで状態を更新します。",
      "ファイル準備を再実行",
    ],
    [
      "EXTRACT",
      "抽出",
      "抽出を開始しました。完了まで状態を更新します。",
      "抽出を実行しています。完了まで状態を更新します。",
      "抽出を再実行",
    ],
    [
      "CHUNK",
      "Chunk 作成",
      "Chunk 作成を開始しました。完了まで状態を更新します。",
      "Chunk 作成を実行しています。完了まで状態を更新します。",
      "Chunk 作成を再実行",
    ],
    [
      "INDEX",
      "Embedding / 索引",
      "Embedding / 索引を開始しました。完了まで状態を更新します。",
      "Embedding / 索引を実行しています。完了まで状態を更新します。",
      "Embedding / 索引を再実行",
    ],
  ])("%s の phase 文言を統一する", (phase, label, started, running, retry) => {
    expect(t(phaseLabelKey(phase))).toBe(label);
    expect(t(phaseStartedMessageKey(phase))).toBe(started);
    expect(t(phaseRunningMessageKey(phase))).toBe(running);
    expect(t(phaseRetryLabelKey(phase))).toBe(retry);
  });
});

describe("resolveDocumentActionPlan", () => {
  const resolve = (
    status: FileStatus,
    overrides: Partial<Parameters<typeof resolveDocumentActionPlan>[0]> = {}
  ) =>
    resolveDocumentActionPlan({
      status,
      activeJob: false,
      latestFailedPhase: null,
      hasPreparedArtifact: true,
      hasExtraction: true,
      hasChunkSet: true,
      ...overrides,
    });

  it("UPLOADED はファイル準備だけを実行できる", () => {
    expect(resolve("UPLOADED")).toEqual({
      primary: { kind: "enqueue", phase: "PREPROCESS" },
      reprocessPhases: [],
    });
  });

  it.each<FileStatus>(["PREPROCESSING", "INGESTING", "CHUNKING", "INDEXING"])(
    "%s の処理中は操作を表示しない",
    (status) => {
      expect(resolve(status)).toEqual({
        primary: null,
        reprocessPhases: [],
      });
    }
  );

  it("active job があれば安定状態でも操作を表示しない", () => {
    expect(resolve("INDEXED", { activeJob: true })).toEqual({
      primary: null,
      reprocessPhases: [],
    });
  });

  it("選択中レシピが無ければ status に関わらず操作を表示しない", () => {
    expect(resolve("REVIEW", { hasSelectedRecipe: false })).toEqual({
      primary: null,
      reprocessPhases: [],
    });
    expect(resolve("UPLOADED", { hasSelectedRecipe: false })).toEqual({
      primary: null,
      reprocessPhases: [],
    });
  });

  it("PREPROCESSED の成果物が有効なら承認の直後にファイル準備再処理を出す", () => {
    expect(resolve("PREPROCESSED")).toEqual({
      primary: { kind: "approve" },
      reprocessPhases: ["PREPROCESS"],
    });
  });

  it("PREPROCESSED の成果物が無ければファイル準備の再実行だけを出す", () => {
    expect(resolve("PREPROCESSED", { hasPreparedArtifact: false })).toEqual({
      primary: { kind: "retry", phase: "PREPROCESS" },
      reprocessPhases: [],
    });
  });

  it("REVIEW は承認と、前提を満たす再処理だけを出す", () => {
    expect(resolve("REVIEW")).toEqual({
      primary: { kind: "approve" },
      reprocessPhases: ["PREPROCESS", "EXTRACT"],
    });
    expect(resolve("REVIEW", { hasPreparedArtifact: false })).toEqual({
      primary: { kind: "approve" },
      reprocessPhases: ["PREPROCESS"],
    });
  });

  it("CHUNKED と INDEXED は前提を満たす段階だけ再処理できる", () => {
    expect(resolve("CHUNKED")).toEqual({
      primary: { kind: "approve" },
      reprocessPhases: ["PREPROCESS", "EXTRACT", "CHUNK"],
    });
    expect(resolve("INDEXED")).toEqual({
      primary: null,
      reprocessPhases: ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"],
    });
    expect(
      resolve("INDEXED", {
        hasPreparedArtifact: false,
        hasExtraction: false,
        hasChunkSet: false,
      })
    ).toEqual({
      primary: null,
      reprocessPhases: ["PREPROCESS"],
    });
  });

  it.each<[IngestionJobPhase, IngestionJobPhase, IngestionJobPhase[]]>([
    ["PREPROCESS", "PREPROCESS", []],
    ["EXTRACT", "EXTRACT", ["PREPROCESS"]],
    ["CHUNK", "CHUNK", ["PREPROCESS", "EXTRACT"]],
    ["INDEX", "INDEX", ["PREPROCESS", "EXTRACT", "CHUNK"]],
  ])("ERROR(%s) は %s と前段の再処理を出す", (failedPhase, retryPhase, earlierPhases) => {
    expect(resolve("ERROR", { latestFailedPhase: failedPhase })).toEqual({
      primary: { kind: "retry", phase: retryPhase },
      reprocessPhases: earlierPhases,
    });
  });

  it("ERROR は不足成果物に応じて前段へ戻し、不明 phase はファイル準備へ戻す", () => {
    expect(
      resolve("ERROR", {
        latestFailedPhase: "INDEX",
        hasChunkSet: false,
      }).primary
    ).toEqual({ kind: "retry", phase: "CHUNK" });
    expect(
      resolve("ERROR", {
        latestFailedPhase: "CHUNK",
        hasExtraction: false,
      }).primary
    ).toEqual({ kind: "retry", phase: "EXTRACT" });
    expect(
      resolve("ERROR", {
        latestFailedPhase: "EXTRACT",
        hasPreparedArtifact: false,
      }).primary
    ).toEqual({ kind: "retry", phase: "PREPROCESS" });
    expect(resolve("ERROR", { latestFailedPhase: null }).primary).toEqual({
      kind: "retry",
      phase: "PREPROCESS",
    });
  });
});

describe("resolveLatestJobsByPhase", () => {
  it("工程順に各工程の最新(先頭一致)ジョブを返す", () => {
    const jobs = [
      { id: "j4", phase: "INDEX" as IngestionJobPhase },
      { id: "j3", phase: "CHUNK" as IngestionJobPhase },
      { id: "j2b", phase: "EXTRACT" as IngestionJobPhase },
      { id: "j2a", phase: "EXTRACT" as IngestionJobPhase },
      { id: "j1", phase: "PREPROCESS" as IngestionJobPhase },
    ];
    const rows = resolveLatestJobsByPhase(jobs);
    expect(rows.map((row) => row.phase)).toEqual([
      "PREPROCESS",
      "EXTRACT",
      "CHUNK",
      "INDEX",
    ]);
    expect(rows.map((row) => row.job?.id ?? null)).toEqual(["j1", "j2b", "j3", "j4"]);
  });

  it("未実行工程は job=null の行になる", () => {
    const rows = resolveLatestJobsByPhase([
      { id: "j1", phase: "PREPROCESS" as IngestionJobPhase },
    ]);
    expect(rows[0].job?.id).toBe("j1");
    expect(rows.slice(1).every((row) => row.job === null)).toBe(true);
  });
});

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
