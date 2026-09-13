import { StatusBadge as UiStatusBadge, type StatusVariant } from "@engchina/production-ready-ui";
import { Hourglass, type LucideIcon } from "lucide-react";

import { t } from "@/lib/i18n";
import type { FileStatus } from "@/lib/api";

export type { FileStatus };

/**
 * RAG のファイル処理状態 → 共有バッジの variant / アイコンの対応表。
 * 処理中（機械が進めている）は info + 砂時計、人の確認待ち（REVIEW）は warning にする。
 * 非推奨の pending（warning の別名）は使わない。
 */
const STATUS_BADGE: Record<FileStatus, { variant: StatusVariant; icon?: LucideIcon }> = {
  UPLOADED: { variant: "neutral" },
  PREPROCESSING: { variant: "info", icon: Hourglass },
  PREPROCESSED: { variant: "info" },
  INGESTING: { variant: "info", icon: Hourglass },
  REVIEW: { variant: "warning" },
  CHUNKING: { variant: "info", icon: Hourglass },
  CHUNKED: { variant: "info" },
  INDEXING: { variant: "info", icon: Hourglass },
  INDEXED: { variant: "success" },
  ERROR: { variant: "danger" },
};

/** ステータスバッジ（ファイル処理状態をアイコン + 日本語で表示）。 */
export function StatusBadge({ status }: { status: FileStatus }) {
  const { variant, icon } = STATUS_BADGE[status];
  return <UiStatusBadge variant={variant} icon={icon ?? true} label={t(`status.${status}`)} />;
}
