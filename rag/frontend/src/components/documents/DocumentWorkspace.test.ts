import { describe, expect, it } from "vitest";

import {
  resolveIngestionParserDisplay,
  resolveIngestionProgressSummary,
} from "./DocumentWorkspace";

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
