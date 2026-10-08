import type { FileStatus, IngestionJob } from "@/lib/api";

/**
 * 文書インデックス（FileListClient）の純粋ロジック。Vitest で境界を検証する（#281）。
 */

/** 状態の絞り込み。backend の FileStatus をすべて選べるようにする（PREPROCESSED を含む）。 */
export const FILE_LIST_FILTERS: readonly (FileStatus | "ALL")[] = [
  "ALL",
  "UPLOADED",
  "PREPROCESSING",
  "PREPROCESSED",
  "INGESTING",
  "REVIEW",
  "CHUNKING",
  "CHUNKED",
  "INDEXING",
  "INDEXED",
  "ERROR",
];

/** 一覧の検索語の上限。backend の `q`（max_length=200）と同じ値にする。 */
export const FILE_LIST_QUERY_MAX_LENGTH = 200;

export interface FileListView {
  filter: FileStatus | "ALL";
  q: string;
  knowledgeBaseId: string;
  offset: number;
}

export const INITIAL_FILE_LIST_VIEW: FileListView = {
  filter: "ALL",
  q: "",
  knowledgeBaseId: "ALL",
  offset: 0,
};

export function isFileListView(value: unknown): value is FileListView {
  const view = value as FileListView;
  return (
    typeof view === "object" &&
    view !== null &&
    (FILE_LIST_FILTERS as unknown[]).includes(view.filter) &&
    typeof view.q === "string" &&
    view.q.length <= FILE_LIST_QUERY_MAX_LENGTH &&
    typeof view.knowledgeBaseId === "string" &&
    Number.isInteger(view.offset) &&
    view.offset >= 0
  );
}

export type EnqueueOutcome =
  | { kind: "queued"; job: IngestionJob }
  | { kind: "skipped"; job: IngestionJob }
  | { kind: "failed"; message: string };

/** 取込 job の投入結果を分類する。SKIPPED は「何も起きない」ように見えるため、理由を知らせる。 */
export function classifyEnqueuedJob(job: IngestionJob): EnqueueOutcome {
  return job.status === "SKIPPED" ? { kind: "skipped", job } : { kind: "queued", job };
}

export interface EnqueueSummary {
  queued: number;
  skipped: number;
  failed: number;
  /** 最初にスキップされた job の理由（表示用のラベルは呼び出し側で解決する）。 */
  firstSkipReason: string | null;
  firstError: string | null;
}

/** 一括投入の結果をまとめる。部分失敗やスキップを黙って成功扱いにしない。 */
export function summarizeEnqueueOutcomes(outcomes: readonly EnqueueOutcome[]): EnqueueSummary {
  const summary: EnqueueSummary = {
    queued: 0,
    skipped: 0,
    failed: 0,
    firstSkipReason: null,
    firstError: null,
  };
  for (const outcome of outcomes) {
    if (outcome.kind === "queued") {
      summary.queued += 1;
    } else if (outcome.kind === "skipped") {
      summary.skipped += 1;
      summary.firstSkipReason = summary.firstSkipReason ?? outcome.job.skip_reason ?? null;
    } else {
      summary.failed += 1;
      summary.firstError = summary.firstError ?? outcome.message;
    }
  }
  return summary;
}

export type DeleteOutcome =
  | { kind: "deleted"; warnings: readonly string[] }
  | { kind: "failed"; message: string };

export interface DeleteSummary {
  deleted: number;
  /** 削除できたが、保存先のファイルの後始末の警告があった件数（#699）。 */
  warned: number;
  failed: number;
  firstWarning: string | null;
  firstError: string | null;
}

/** 一括削除の結果をまとめる。後始末の警告を、1 件の削除と同じく成功として黙らせない（#281 / #699）。 */
export function summarizeDeleteOutcomes(outcomes: readonly DeleteOutcome[]): DeleteSummary {
  const summary: DeleteSummary = {
    deleted: 0,
    warned: 0,
    failed: 0,
    firstWarning: null,
    firstError: null,
  };
  for (const outcome of outcomes) {
    if (outcome.kind === "failed") {
      summary.failed += 1;
      summary.firstError = summary.firstError ?? outcome.message;
      continue;
    }
    summary.deleted += 1;
    if (outcome.warnings.length > 0) {
      summary.warned += 1;
      summary.firstWarning = summary.firstWarning ?? outcome.warnings.join(" ");
    }
  }
  return summary;
}
