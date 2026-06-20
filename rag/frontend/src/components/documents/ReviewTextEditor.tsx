import { useMemo, useState } from "react";

import type { DocumentApproveRequest, DocumentElement } from "@/lib/api";
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
 * bbox・構造はサーバ側を保持し、要素テキストと raw_text のみ差し替える。
 * 変更があった項目だけを `onChange` で親へ通知する(承認時に送信)。
 */
export function ReviewTextEditor({
  extraction,
  onChange,
}: {
  extraction: Record<string, unknown>;
  onChange: (edits: DocumentApproveRequest) => void;
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

  const [elementTexts, setElementTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(originalElementText.entries())
  );
  const [cellTexts, setCellTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(originalCellText.entries())
  );
  const [rawText, setRawText] = useState(parsed.rawText);

  function emit(
    nextTexts: Record<string, string>,
    nextCells: Record<string, string>,
    nextRaw: string
  ) {
    const element_edits = Array.from(originalElementText.entries())
      .filter(([id, original]) => nextTexts[id] !== original)
      .map(([id]) => ({ element_id: id, text: nextTexts[id] }));
    const table_cell_edits = editableTables.flatMap((table) =>
      table.cells
        .filter((cell) => {
          const key = cellKey(table.table_id, cell.row, cell.col);
          return nextCells[key] !== originalCellText.get(key);
        })
        .map((cell) => ({
          table_id: table.table_id,
          row: cell.row,
          col: cell.col,
          text: nextCells[cellKey(table.table_id, cell.row, cell.col)],
        }))
    );
    const payload: DocumentApproveRequest = { element_edits, table_cell_edits };
    if (nextRaw !== parsed.rawText) payload.raw_text = nextRaw;
    onChange(payload);
  }

  function handleElementChange(id: string, value: string) {
    const next = { ...elementTexts, [id]: value };
    setElementTexts(next);
    emit(next, cellTexts, rawText);
  }

  function handleCellChange(key: string, value: string) {
    const next = { ...cellTexts, [key]: value };
    setCellTexts(next);
    emit(elementTexts, next, rawText);
  }

  function handleRawChange(value: string) {
    setRawText(value);
    emit(elementTexts, cellTexts, value);
  }

  const editableElements = parsed.elements.filter((element) => editableElementId(element));

  return (
    <div className="space-y-4 rounded-lg border border-border bg-background p-4">
      <p className="text-xs text-muted">{t("flow.review.edit.hint")}</p>

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
                    value={elementTexts[id] ?? ""}
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
                                    value={cellTexts[key] ?? ""}
                                    onChange={(event) => handleCellChange(key, event.target.value)}
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

      <section className="space-y-1">
        <label htmlFor="review-edit-raw-text" className="text-sm font-semibold text-foreground">
          {t("flow.review.edit.rawText")}
        </label>
        <textarea
          id="review-edit-raw-text"
          value={rawText}
          onChange={(event) => handleRawChange(event.target.value)}
          rows={4}
          className={TEXTAREA_CLASS}
        />
      </section>
    </div>
  );
}
