import { ApiError, type IngestionJobPhase } from "@/lib/api";

/** 言語資源の版の内容（公開時の設定・差分）を、現在の契約で読めないか。backend は 409 で返す。 */
export function isUnreadableReleaseError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

export const ALL_START_PHASES = ["PREPROCESS", "EXTRACT", "CHUNK", "INDEX"] as const satisfies readonly IngestionJobPhase[];

/** 読めない版で作った抽出結果・Chunk は再利用できないので、ファイル準備か抽出から始める。 */
export const UNREADABLE_START_PHASES = ["PREPROCESS", "EXTRACT"] as const satisfies readonly IngestionJobPhase[];

/** 読めない版を使う対象の開始工程。ファイル準備はそのまま、それより後なら抽出に戻す。 */
export function startPhaseForUnreadable(phase: IngestionJobPhase): IngestionJobPhase {
  return phase === "PREPROCESS" ? "PREPROCESS" : "EXTRACT";
}

/** 選べない工程が選ばれていたら未選択として扱う（読めないと分かった後に CHUNK / INDEX を残さない）。 */
export function selectablePhase(phase: IngestionJobPhase | "", unreadable: boolean): IngestionJobPhase | "" {
  if (!phase) return "";
  const allowed: readonly IngestionJobPhase[] = unreadable ? UNREADABLE_START_PHASES : ALL_START_PHASES;
  return allowed.includes(phase) ? phase : "";
}
