import { Fragment, type ReactNode } from "react";
import { Table2 } from "lucide-react";
import {
  ChatResultTable,
  splitMarkdownTables,
  type ChatResultTableLabels,
  type TabularData,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

/**
 * Agent のデータの表（#1158）。ツールの結果・成果物・回答の Markdown の表を、NL2SQL のチャットと同じ共通の
 * 結果の表（`ChatResultTable`。#1154）で出す。表の見た目と振る舞い（要約・表の中のスクロール・すべての行・CSV・
 * NULL の表示）は部品が持ち、ここは何の表か（表の名前）だけを渡す。
 */

function tableLabels(name: string): Partial<ChatResultTableLabels> {
  return {
    tableLabel: name,
    scrollLabel: t("resultTable.scrollLabel", { name }),
    sheetTitle: (count) => t("resultTable.sheetTitle", { name, count }),
    close: t("resultTable.close", { name }),
  };
}

/** 表の形の結果（ツールの結果・成果物）。 */
export function ResultTable({ data, name, testId }: { data: TabularData; name: string; testId?: string }) {
  return (
    <ChatResultTable
      columns={data.columns}
      rows={data.rows}
      truncated={data.truncated}
      totalRowCount={data.totalRowCount}
      elapsedMs={data.elapsedMs}
      labels={tableLabels(name)}
      testId={testId}
    />
  );
}

/** チャットの回答の下に出すツールの結果の表（見出しはツールの名前）。 */
export function ToolResultTable({ data, toolName, testId }: { data: TabularData; toolName: string; testId?: string }) {
  const name = t("resultTable.toolName", { tool: toolName });
  return (
    <section className="min-w-0 space-y-2" aria-label={name} data-testid={testId}>
      <p className="flex min-w-0 items-center gap-1.5 text-xs text-fg-muted">
        <Table2 size={14} aria-hidden="true" className="shrink-0" />
        <span className="min-w-0 break-all">{name}</span>
      </p>
      <ResultTable data={data} name={name} testId={testId ? `${testId}-result` : undefined} />
    </section>
  );
}

/**
 * 回答の本文。Markdown の表は共通の結果の表で出し、表以外の部分は `renderText` で今までどおり出す。
 * 表が無い本文は `renderText` 1 つだけになる。
 */
export function AnswerBody({
  text,
  renderText,
  testId,
}: {
  text: string;
  renderText: (text: string) => ReactNode;
  testId?: string;
}) {
  const segments = splitMarkdownTables(text);
  // 表に 1 から番号を振る（表が 1 つなら番号を付けない）。
  const tableNumbers = segments.map((segment, index) =>
    segment.kind === "table" ? segments.slice(0, index + 1).filter((item) => item.kind === "table").length : 0
  );
  const tableCount = tableNumbers.filter((value) => value > 0).length;
  if (tableCount === 0) return <>{renderText(text)}</>;
  return (
    <div className="min-w-0 space-y-3" data-testid={testId}>
      {segments.map((segment, index) =>
        segment.kind === "text" ? (
          <Fragment key={index}>{renderText(segment.text)}</Fragment>
        ) : (
          <ResultTable
            key={index}
            data={segment.data}
            name={
              tableCount === 1
                ? t("resultTable.answerName")
                : t("resultTable.answerNameIndexed", { index: tableNumbers[index] })
            }
            testId={testId ? `${testId}-table-${tableNumbers[index]}` : undefined}
          />
        )
      )}
    </div>
  );
}
