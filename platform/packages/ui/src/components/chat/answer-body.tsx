import { Fragment, type ReactNode } from "react";

import { splitMarkdownTables, type SplitMarkdownTablesOptions } from "../../lib/tabular-data";
import { cn } from "../../lib/utils";
import { ResultTable, type ResultTableLabels } from "../data/result-table";
import { MarkdownText } from "../ui/markdown-text";

export interface AnswerBodyLabels {
  /** 本文の表の名前（読み上げ・シートの見出しに使う）。本文の表が 1 つなら `number` は null。 */
  tableName: (number: number | null) => string;
  /** その表の `ResultTable` の文言（表の名前を入れた読み上げ・シートの見出しなど）。 */
  table?: (name: string) => Partial<ResultTableLabels>;
}

export const DEFAULT_ANSWER_BODY_LABELS: AnswerBodyLabels = {
  tableName: (number) => (number === null ? "回答の表" : `回答の表 ${number}`),
};

export interface AnswerBodyProps {
  /** 回答の本文（表を含みうる文字列）。 */
  text: string;
  /** 本文の最上位の見出しの段（`renderText` を渡さないとき。既定 3）。 */
  headingLevel?: 2 | 3 | 4 | 5 | 6;
  /** 表以外の部分の描き方（既定は `MarkdownText`）。 */
  renderText?: (text: string) => ReactNode;
  /** 表の切り出し方（`splitMarkdownTables` の指定）。 */
  splitOptions?: SplitMarkdownTablesOptions;
  labels?: AnswerBodyLabels;
  className?: string;
  /** 外側の data-testid。表は `{testId}-table-{番号}` になる。 */
  testId?: string;
  /** 表のセルの描き方（`ResultTable` の `renderCell`）。 */
  renderTableCell?: (cell: AnswerBodyTableCell) => ReactNode | undefined;
}

/** `renderTableCell` に渡す表のセル。`table` は本文の中の表の番号（1 から）と列。 */
export interface AnswerBodyTableCell {
  value: unknown;
  rowIndex: number;
  columnIndex: number;
  table: { number: number; columns: readonly { name: string }[] };
}

/**
 * 回答の本文（#1158 / #1808 / #4734）。Markdown の表は共通の結果の表（`ResultTable`）で出し、表以外の部分は
 * 共通の Markdown の本文（`MarkdownText`）か、製品が渡す描き方で描く。3 製品で表の出し方を同じにするため、表の
 * 切り出しと番号の振り方をここに置く。
 */
export function AnswerBody({
  text,
  headingLevel,
  renderText,
  splitOptions,
  labels = DEFAULT_ANSWER_BODY_LABELS,
  className,
  testId,
  renderTableCell,
}: AnswerBodyProps) {
  const segments = splitMarkdownTables(text, splitOptions);
  // 表に 1 から番号を振る（表が 1 つなら番号を付けない）。
  const tableNumbers = segments.map((segment, index) =>
    segment.kind === "table" ? segments.slice(0, index + 1).filter((item) => item.kind === "table").length : 0,
  );
  const tableCount = tableNumbers.filter((value) => value > 0).length;
  return (
    <div className={cn("min-w-0 space-y-3", className)} data-testid={testId}>
      {segments.map((segment, index) => {
        if (segment.kind === "text") {
          return renderText ? (
            <Fragment key={index}>{renderText(segment.text)}</Fragment>
          ) : (
            <MarkdownText key={index} text={segment.text} headingLevel={headingLevel} />
          );
        }
        const name = labels.tableName(tableCount === 1 ? null : tableNumbers[index]);
        return (
          <ResultTable
            key={index}
            renderCell={
              renderTableCell
                ? (value, rowIndex, columnIndex) =>
                    renderTableCell({ value, rowIndex, columnIndex, table: { number: tableNumbers[index], columns: segment.data.columns } })
                : undefined
            }
            columns={segment.data.columns}
            rows={segment.data.rows}
            truncated={segment.data.truncated}
            totalRowCount={segment.data.totalRowCount}
            elapsedMs={segment.data.elapsedMs}
            labels={labels.table?.(name) ?? { tableLabel: name }}
            testId={testId ? `${testId}-table-${tableNumbers[index]}` : undefined}
          />
        );
      })}
    </div>
  );
}
