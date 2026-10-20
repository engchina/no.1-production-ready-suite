import { describe, expect, it } from "vitest";

import type { IngestionJob } from "@/lib/api";

import {
  classifyEnqueuedJob,
  FILE_LIST_FILTERS,
  fileListFilterLabel,
  fileListFilterParams,
  INITIAL_FILE_LIST_VIEW,
  isFileListView,
  summarizeDeleteOutcomes,
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
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, filter: "QUEUED_UNKNOWN" })).toBe(false);
  });

  // #2677: 行に出る状態（順番待ちを含む）をすべて、処理の順に並べる。すべては先頭、エラーは最後。
  it("選択肢は行に出る状態を処理の順に並べ、順番待ちは各工程の前に置く", () => {
    expect(FILE_LIST_FILTERS.map(fileListFilterLabel)).toEqual([
      "すべて",
      "ファイル準備待ち",
      "ファイル準備の順番待ち",
      "ファイル準備中",
      "ファイル準備確認待ち",
      "抽出の順番待ち",
      "解析（抽出）中",
      "抽出確認待ち",
      "Chunk 作成の順番待ち",
      "Chunk 作成中",
      "Chunk 確認待ち",
      "Embedding / 索引の順番待ち",
      "Embedding / 索引中",
      "索引済み",
      "エラー",
    ]);
    expect(new Set(FILE_LIST_FILTERS).size).toBe(FILE_LIST_FILTERS.length);
  });

  it("順番待ちの絞り込みも作業状態から復元する", () => {
    for (const filter of ["QUEUED_PREPROCESS", "QUEUED_EXTRACT", "QUEUED_CHUNK", "QUEUED_INDEX"]) {
      expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, filter })).toBe(true);
    }
  });

  it("絞り込みの値を一覧の API の status / queued_phase に分ける", () => {
    expect(fileListFilterParams("ALL")).toEqual({});
    expect(fileListFilterParams("UPLOADED")).toEqual({ status: "UPLOADED" });
    expect(fileListFilterParams("ERROR")).toEqual({ status: "ERROR" });
    expect(fileListFilterParams("QUEUED_PREPROCESS")).toEqual({ queued_phase: "PREPROCESS" });
    expect(fileListFilterParams("QUEUED_INDEX")).toEqual({ queued_phase: "INDEX" });
  });

  // #1891: 表示件数を作業状態に残す。
  it("表示件数は選べる段（10 / 25 / 50 / 100）だけを復元する", () => {
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, limit: 50 })).toBe(true);
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, limit: 30 })).toBe(false);
    expect(isFileListView({ ...INITIAL_FILE_LIST_VIEW, limit: undefined })).toBe(false);
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

describe("summarizeDeleteOutcomes", () => {
  it("後始末の警告のあった削除を、成功と分けて数える", () => {
    expect(
      summarizeDeleteOutcomes([
        { kind: "deleted", warnings: [] },
        { kind: "deleted", warnings: ["原本を削除できませんでした。", "再試行してください。"] },
        { kind: "deleted", warnings: ["artifact を削除できませんでした。"] },
        { kind: "failed", message: "実行中です。" },
      ])
    ).toEqual({
      deleted: 3,
      warned: 2,
      failed: 1,
      firstWarning: "原本を削除できませんでした。 再試行してください。",
      firstError: "実行中です。",
    });
  });
});
