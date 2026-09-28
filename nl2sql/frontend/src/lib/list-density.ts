// 一覧の表示密度の定数は @engchina/production-ready-ui に移した（#265）。新しいコードはパッケージのルートから import する。
// このファイルは、並行作業（#325）が触る SystemTablesCard の import を変えないための再公開で、#325 の後に削除する。
export {
  INFORMATION_LIST_VISIBLE_ROWS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  INFORMATION_TABLE_FIXED_VISIBLE_ROWS,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_LIST_ROW_CLASS,
  INFORMATION_LIST_SCROLL_CLASS,
  INFORMATION_COMPACT_LIST_FIVE_ROW_SCROLL_CLASS,
  INFORMATION_TABLE_FOCUS_CLASS,
} from "@engchina/production-ready-ui";
