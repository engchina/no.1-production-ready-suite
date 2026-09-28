import { describe, expect, it } from "vitest";

import type { IngestionJob } from "@/lib/api";

import {
  classifyEnqueuedJob,
  FILE_LIST_FILTERS,
  INITIAL_FILE_LIST_VIEW,
  isFileListView,
  outOfRangeOffset,
  summarizeEnqueueOutcomes,
} from "./FileListClient.logic";

function job(overrides: Partial<IngestionJob>): IngestionJob {
  return {
    id: "job-1",
    document_id: "doc-1",
    recipe_id: null,
    recipe_revision: null,
    status: "QUEUED",
    phase: "PREPROCESS",
    parser_profile: "local_text_structure",
    quality_warnings: [],
    skip_reason: null,
    error_message: null,
    attempt_count: 0,
    max_attempts: 3,
    queued_at: "2026-09-28T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

describe("FILE_LIST_FILTERS / isFileListView", () => {
  it("ファイル準備確認待ち(PREPROCESSED)でも絞り込める", () => {
    expect(FILE_LIST_FILTERS).toContain("PREPROCESSED");
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, filter: "PREPROCESSED" })).toBe(true);
  });

  it("backend が受け付けない長さの検索語は復元しない", () => {
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, q: "a".repeat(200) })).toBe(true);
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, q: "a".repeat(201) })).toBe(false);
  });

  it("不正な offset・状態は復元しない", () => {
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, offset: -20 })).toBe(false);
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, filter: "UNKNOWN" })).toBe(false);
  });
});

describe("outOfRangeOffset", () => {
  it("範囲内なら補正しない", () => {
    expect(outOfRangeOffset({ offset: 0, total: 0, limit: 20 })).toBeNull();
    expect(outOfRangeOffset({ offset: 20, total: 21, limit: 20 })).toBeNull();
  });

  it("最後のページの最後の 1 件を削除したら、残っている最後のページへ戻す", () => {
    expect(outOfRangeOffset({ offset: 20, total: 20, limit: 20 })).toBe(0);
    expect(outOfRangeOffset({ offset: 60, total: 41, limit: 20 })).toBe(40);
  });

  it("1 件も無くなったら先頭へ戻す", () => {
    expect(outOfRangeOffset({ offset: 40, total: 0, limit: 20 })).toBe(0);
  });
});

describe("classifyEnqueuedJob / summarizeEnqueueOutcomes", () => {
  it("SKIPPED の job はスキップとして分類する", () => {
    expect(classifyEnqueuedJob(job({ status: "QUEUED" })).kind).toBe("queued");
    expect(
      classifyEnqueuedJob(job({ status: "SKIPPED", skip_reason: "duplicate_content" })).kind
    ).toBe("skipped");
  });

  it("投入・スキップ・失敗を数え、最初の理由を残す", () => {
    const summary = summarizeEnqueueOutcomes([
      classifyEnqueuedJob(job({ id: "a" })),
      classifyEnqueuedJob(job({ id: "b", status: "SKIPPED", skip_reason: "duplicate_content" })),
      { kind: "failed", message: "このドキュメントは取込待ちまたは取込中です。" },
      { kind: "failed", message: "2 件目の失敗" },
    ]);
    expect(summary).toEqual({
      queued: 1,
      skipped: 1,
      failed: 2,
      firstSkipReason: "duplicate_content",
      firstError: "このドキュメントは取込待ちまたは取込中です。",
    });
  });
});
