import { useMemo } from "react";

import type { DocumentElement, DocumentReviewEditsRequest } from "@/lib/api";
import { parseStructuredExtraction } from "@/lib/extraction";
import { t } from "@/lib/i18n";

function cellKey(tableId: string, row: number, col: number): string {
  return `${tableId}::${row}::${col}`;
}

const TEXTAREA_CLASS =
  "min-h-20 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm " +
  "leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted/70 " +
  "focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 " +
  "focus-visible:outline-ring";

function editableElementId(element: DocumentElement): string | null {
  return element.element_id ?? null;
}

/**
 * REVIEW(確認待ち)中の人手テキスト修正エディタ。
 * 要素・表セルのテキストだけを編集する。
 * bbox・表構造・ページ情報はサーバ側で保持する。
 * 変更があった項目だけを `onChange` で親へ通知する(明示保存時に送信)。
 */
export function ReviewTextEditor({
  extraction,
  edits,
  onChange,
}: {
  extraction: Record<string, unknown>;
  edits: DocumentReviewEditsRequest;
  onChange: (edits: DocumentReviewEditsRequest) => void;
}) {
  const parsed = useMemo(() => parseStructuredExtraction(extraction), [extraction]);
  const originalElementText = useMemo(() => {
    const map = new Map<string, string>();
    for (const element of parsed.elements) {
      const id = editableElementId(element);
      if (id) map.set(id, element.text);
    }
    return map;
  }, [parsed.elements]);

  const editableTables = useMemo(
    () => parsed.tables.filter((table) => table.table_id && table.cells.length > 0),
    [parsed.tables]
  );
  const originalCellText = useMemo(() => {
    const map = new Map<string, string>();
    for (const table of editableTables) {
      for (const cell of table.cells) {
        map.set(cellKey(table.table_id, cell.row, cell.col), cell.text);
      }
    }
    return map;
  }, [editableTables]);
  const structuredTableKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const table of editableTables) {
      keys.add(table.table_id);
      if (table.element_id) keys.add(table.element_id);
    }
    return keys;
  }, [editableTables]);

  function handleElementChange(id: string, value: string) {
    const element_edits = (edits.element_edits ?? []).filter((edit) => edit.element_id !== id);
    if (value !== originalElementText.get(id)) element_edits.push({ element_id: id, text: value });
    onChange({ ...edits, element_edits });
  }

  function handleCellChange(tableId: string, row: number, col: number, value: string) {
    const table_cell_edits = (edits.table_cell_edits ?? []).filter(
      (edit) => edit.table_id !== tableId || edit.row !== row || edit.col !== col
    );
    if (value !== originalCellText.get(cellKey(tableId, row, col))) {
      table_cell_edits.push({ table_id: tableId, row, col, text: value });
    }
    onChange({ ...edits, table_cell_edits });
  }

  const editableElements = parsed.elements.filter((element) => {
    const id = editableElementId(element);
    if (!id) return false;
    const metadataTableId = element.metadata?.table_id;
    const hasStructuredTable =
      element.kind === "table" &&
      (structuredTableKeys.has(id) ||
        (typeof metadataTableId === "string" && structuredTableKeys.has(metadataTableId)));
    return !hasStructuredTable;
  });

  return (
    <div className="space-y-4 rounded-lg border border-border bg-background p-4">
      <p className="text-xs text-muted">{t("flow.review.edit.structuredHint")}</p>

      {editableElements.length > 0 ? (
        <section aria-label={t("flow.review.edit.elements")} className="space-y-3">
          <h4 className="text-sm font-semibold text-foreground">
            {t("flow.review.edit.elements")}
          </h4>
          <ol className="max-h-[520px] space-y-3 overflow-auto pr-1">
            {editableElements.map((element) => {
              const id = editableElementId(element) as string;
              const fieldId = `review-edit-${id}`;
              return (
                <li key={id} className="space-y-1">
                  <label
                    htmlFor={fieldId}
                    className="flex flex-wrap items-center gap-2 text-xs text-muted"
                  >
                    <span className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary">
                      {element.kind}
                    </span>
                    {typeof element.page_number === "number" ? (
                      <span className="tnum rounded-full bg-card px-2 py-0.5">
                        {t("flow.extraction.page", { page: element.page_number })}
                      </span>
                    ) : null}
                    {element.section_path?.length ? (
                      <span className="min-w-0 max-w-full break-words rounded-full bg-info-bg px-2 py-0.5 text-info">
                        {element.section_path.join(" > ")}
                      </span>
                    ) : null}
                  </label>
                  <textarea
                    id={fieldId}
                    value={
                      edits.element_edits?.find((edit) => edit.element_id === id)?.text ??
                      element.text
                    }
                    onChange={(event) => handleElementChange(id, event.target.value)}
                    rows={2}
                    className={TEXTAREA_CLASS}
                  />
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      {editableTables.length > 0 ? (
        <section aria-label={t("flow.review.edit.tableCells")} className="space-y-3">
          <h4 className="text-sm font-semibold text-foreground">
            {t("flow.review.edit.tableCells")}
          </h4>
          {editableTables.map((table) => (
            <div key={table.table_id} className="space-y-1">
              {table.caption ? (
                <p className="text-xs text-muted">{table.caption}</p>
              ) : null}
              <div className="overflow-auto rounded-md border border-border">
                <table className="w-full border-collapse text-sm">
                  <tbody>
                    {Array.from(new Set(table.cells.map((cell) => cell.row)))
                      .sort((a, b) => a - b)
                      .map((row) => (
                        <tr key={row}>
                          {table.cells
                            .filter((cell) => cell.row === row)
                            .sort((a, b) => a.col - b.col)
                            .map((cell) => {
                              const key = cellKey(table.table_id, cell.row, cell.col);
                              const fieldId = `review-edit-cell-${key}`;
                              return (
                                <td key={key} className="border border-border p-1 align-top">
                                  <label htmlFor={fieldId} className="sr-only">
                                    {t("flow.review.edit.tableCellLabel", {
                                      row: cell.row + 1,
                                      col: cell.col + 1,
                                    })}
                                  </label>
                                  <textarea
                                    id={fieldId}
                                    value={
                                      edits.table_cell_edits?.find(
                                        (edit) =>
                                          edit.table_id === table.table_id &&
                                          edit.row === cell.row &&
                                          edit.col === cell.col
                                      )?.text ?? cell.text
                                    }
                                    onChange={(event) =>
                                      handleCellChange(
                                        table.table_id,
                                        cell.row,
                                        cell.col,
                                        event.target.value
                                      )
                                    }
                                    rows={1}
                                    className={`${TEXTAREA_CLASS} min-h-9`}
                                  />
                                </td>
                              );
                            })}
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </section>
      ) : null}
    </div>
  );
}
