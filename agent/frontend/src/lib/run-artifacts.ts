import type { Artifact } from "@/lib/api";
import { ANSWER_KIND } from "@/lib/answer-review";

/** ファイルの成果物（#5837。backend の `artifact_file_store.FILE_ARTIFACT_KIND`）。 */
export const FILE_ARTIFACT_KIND = "file";

/**
 * 実行の成果物の分け方（#2403）。
 *
 * 実行の詳細の「成果物」の節に出すのは、実行が利用者に渡す出力（表の結果・ファイル・レポートなど）だけにする。
 * 種類はこの一覧で決め、一覧に無い種類（支援タスクの状態・検索の途中の結果・回答の根拠・不明な種類）は
 * 内部の記録として、実行の詳細の「監査ログ」で元の JSON まで見る（保存と API は変えない）。
 * 回答（`answer`）は「回答」の節に出すので、どちらにも入れない。
 *
 * 利用者向けの出力の種類（ファイル・レポートなど）を backend に足したら、この一覧にも足す。
 */
export const USER_OUTPUT_ARTIFACT_KINDS: ReadonlySet<string> = new Set([
  // 表の結果（NL2SQL のツールの結果など。SQL と表・CSV のダウンロード）。
  "structured_table",
  // ツールが作ったファイル（ダウンロードのボタン）。
  FILE_ARTIFACT_KIND,
]);

/** ファイルの形式（backend の `FILE_ARTIFACT_EXTENSIONS` と同じ 4 つ。それ以外は `other`）。 */
export type FileArtifactType = "xlsx" | "docx" | "pptx" | "md" | "other";

const FILE_ARTIFACT_TYPES: Record<string, FileArtifactType> = {
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
  // Markdown（#5867。backend の `MARKDOWN_MIME_TYPE`）。画面ではプレビューせず、ダウンロードだけ。
  "text/markdown; charset=utf-8": "md",
};

/** ファイルの成果物の表示に使う値（中身は成果物に無い。ダウンロードの API で取る）。 */
export interface RunFileArtifact {
  id: string;
  fileName: string;
  type: FileArtifactType;
  /** 大きさ（バイト。記録の無いものは null）。 */
  sizeBytes: number | null;
}

/** Run が作ったファイルの成果物（元の順序）。 */
export function runFileArtifacts(
  artifacts: readonly Pick<Artifact, "id" | "kind" | "name" | "content">[]
): RunFileArtifact[] {
  return artifacts
    .filter((artifact) => artifact.kind === FILE_ARTIFACT_KIND)
    .map((artifact) => {
      const { file_name: fileName, mime_type: mimeType, size_bytes: sizeBytes } = artifact.content;
      return {
        id: artifact.id,
        fileName: typeof fileName === "string" && fileName ? fileName : artifact.name,
        type: (typeof mimeType === "string" && FILE_ARTIFACT_TYPES[mimeType]) || "other",
        sizeBytes: typeof sizeBytes === "number" && Number.isFinite(sizeBytes) ? sizeBytes : null,
      };
    });
}

/**
 * 最終回答に付けた回答の注意書き（#2405。`answer` の成果物の `notice`）。付けていない回答・古い Run は null。
 */
export function answerNoticeText(artifacts: readonly Pick<Artifact, "kind" | "content">[]): string | null {
  for (const artifact of [...artifacts].reverse()) {
    if (artifact.kind !== ANSWER_KIND) continue;
    const notice = artifact.content.notice;
    return typeof notice === "string" && notice.trim() ? notice : null;
  }
  return null;
}

/** 利用者が使う出力（「成果物」の節に出す）か。 */
export function isUserOutputArtifact(artifact: Pick<Artifact, "kind">): boolean {
  return USER_OUTPUT_ARTIFACT_KINDS.has(artifact.kind);
}

/** 「成果物」の節に出す成果物（元の順序）。 */
export function userOutputArtifacts<T extends Pick<Artifact, "kind">>(artifacts: readonly T[]): T[] {
  return artifacts.filter(isUserOutputArtifact);
}

/** 監査ログに出す内部の記録（回答と利用者向けの出力を除いた成果物。元の順序）。 */
export function internalRecordArtifacts<T extends Pick<Artifact, "kind">>(artifacts: readonly T[]): T[] {
  return artifacts.filter((artifact) => artifact.kind !== ANSWER_KIND && !isUserOutputArtifact(artifact));
}
