import {
  ResultCell,
  isNumericResultColumn,
  type DataTableColumn,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import type { QueryResults } from "../types";

type Row = Record<string, unknown>;

/**
 * SQL 生成・SELECT SQL の実行の画面の結果の表の列（#1154）。
 *
 * セルの表示はチャットの結果の表（共通の `ChatResultTable`）と同じ `ResultCell` / `isNumericResultColumn` を
 * 使う（NULL は斜体・淡色の「NULL」で空文字と区別し、数値の列は右寄せ）。同じ結果が画面ごとに違って
 * 見えないようにする。
 */
export function resultTableColumns(results: Pick<QueryResults, "columns" | "rows">): DataTableColumn<Row>[] {
  const valuesByColumn = results.rows.map((row) => results.columns.map((column) => row[column]));
  return results.columns.map((column, index) => {
    const numeric = isNumericResultColumn(valuesByColumn, index);
    return {
      key: column,
      header: column,
      align: numeric ? "right" : "left",
      className: numeric ? "tabular-nums" : undefined,
      render: (row: Row) => (
        <ResultCell value={row[column]} variant="full" nullLabel={t("queryResults.null")} />
      ),
    };
  });
}
