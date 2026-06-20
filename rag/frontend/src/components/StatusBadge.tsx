import { StatusBadge as UiStatusBadge, type StatusVariant } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import type { FileStatus } from "@/lib/api";

export type { FileStatus };

/** RAG のファイル処理状態 → 共有バッジの汎用 variant マッピング。 */
const STATUS_VARIANT: Record<FileStatus, StatusVariant> = {
  UPLOADED: "neutral",
  INGESTING: "pending",
  REVIEW: "info",
  INDEXING: "pending",
  INDEXED: "success",
  ERROR: "danger",
};

/** ステータスバッジ（ファイル処理状態を色＋日本語で表示）。 */
export function StatusBadge({ status }: { status: FileStatus }) {
  return <UiStatusBadge variant={STATUS_VARIANT[status]} label={t(`status.${status}`)} />;
}
