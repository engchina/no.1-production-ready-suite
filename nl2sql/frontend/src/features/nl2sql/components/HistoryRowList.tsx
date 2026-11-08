import { INFORMATION_TABLE_VISIBLE_ROWS, ListSkeleton } from "@production-ready/ui";

/**
 * 履歴の行の一覧の読み込み中（初回）の形の Skeleton。フィードバック管理のフィードバック履歴（一覧 + 詳細の一覧の列。
 * #2283）が使う。読み込み中の文言と経過時間は、一覧と詳細の両方を覆う作業領域の先頭が 1 か所だけ出す（#4649）。
 * 行の部品（質問 → 日時・バッジの複数行）は、一覧の列の 1 列の表（`RowTitleButton`）に移した。
 */
export function HistoryRowListSkeleton({ testId }: { testId: string }) {
  return <ListSkeleton rows={INFORMATION_TABLE_VISIBLE_ROWS} rowClassName="h-20" testId={testId} />;
}
