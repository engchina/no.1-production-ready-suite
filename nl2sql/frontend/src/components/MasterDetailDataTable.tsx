import type { MouseEvent, ReactNode } from "react";
import { ChevronDown } from "lucide-react";

import type { DataTableColumn, DataTableSort } from "@engchina/production-ready-ui";

import { cn } from "@/lib/utils";

export type MasterDetailRowKey = string | number;

export function isInteractiveRowTarget(target: EventTarget | null) {
  return (
    target instanceof Element &&
    Boolean(
      target.closest(
        'button,a,input,select,textarea,[role="button"],[role="menuitem"],[data-row-action]'
      )
    )
  );
}

function nextSort(current: DataTableSort | undefined, key: string): DataTableSort {
  if (current?.key === key) {
    return { key, direction: current.direction === "asc" ? "desc" : "asc" };
  }
  return { key, direction: "asc" };
}

function alignClass(align?: DataTableColumn<unknown>["align"]) {
  if (align === "right") return "text-right";
  if (align === "center") return "text-center";
  return "text-left";
}

export function MasterDetailDataTable<T>({
  columns,
  rows,
  getRowKey,
  selectedRowKey,
  onRowSelect,
  getRowAriaLabel,
  sort,
  onSortChange,
  loading = false,
  dense = false,
  empty,
  ariaLabel,
  testId,
  className,
  rowClassName,
  scrollClassName,
  scrollTestId,
  scrollAriaLabel,
}: {
  columns: Array<DataTableColumn<T>>;
  rows: T[];
  getRowKey: (row: T, index: number) => MasterDetailRowKey;
  selectedRowKey?: MasterDetailRowKey | null;
  onRowSelect?: (row: T) => void;
  getRowAriaLabel?: (row: T) => string;
  sort?: DataTableSort;
  onSortChange?: (sort: DataTableSort) => void;
  loading?: boolean;
  dense?: boolean;
  empty?: ReactNode;
  ariaLabel?: string;
  testId?: string;
  className?: string;
  /** データ行と loading 行へ追加する class。共通の行高などを指定する。 */
  rowClassName?: string;
  /** 横スクロール領域へ追加する class。高さ制限や縦スクロールもここへ集約する。 */
  scrollClassName?: string;
  /** スクロール領域を E2E / a11y 検証から特定するための任意 ID。 */
  scrollTestId?: string;
  /** 縦・横スクロール領域として公開するときのアクセシブルネーム。 */
  scrollAriaLabel?: string;
}) {
  const selectable = Boolean(onRowSelect);
  const paddingClass = dense ? "px-3 py-2" : "px-4 py-3";

  const handleRowClick = (event: MouseEvent<HTMLTableRowElement>, row: T) => {
    if (!onRowSelect || isInteractiveRowTarget(event.target)) return;
    onRowSelect(row);
  };

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div
        role={scrollAriaLabel ? "region" : undefined}
        tabIndex={scrollAriaLabel ? 0 : undefined}
        aria-label={scrollAriaLabel}
        className={cn("overflow-x-auto", scrollClassName)}
        data-testid={scrollTestId}
      >
        <table
          className={cn("w-full divide-y divide-border text-left text-sm", className)}
          aria-label={ariaLabel}
          data-testid={testId}
        >
          <thead className="bg-background text-xs text-muted">
            <tr>
              {columns.map((column) => {
                const active = sort?.key === column.key;
                return (
                  <th
                    key={column.key}
                    scope="col"
                    aria-sort={
                      active
                        ? sort?.direction === "asc"
                          ? "ascending"
                          : "descending"
                        : undefined
                    }
                    className={cn(
                      "whitespace-nowrap font-semibold",
                      paddingClass,
                      alignClass(column.align as DataTableColumn<unknown>["align"]),
                      column.className
                    )}
                  >
                    {column.sortable && onSortChange ? (
                      <button
                        type="button"
                        className={cn(
                          "inline-flex items-center gap-1 font-semibold text-foreground hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40",
                          column.align === "right" && "ml-auto",
                          column.align === "center" && "mx-auto"
                        )}
                        onClick={() => onSortChange(nextSort(sort, column.key))}
                      >
                        <span>{column.header}</span>
                        <ChevronDown
                          size={14}
                          className={cn(
                            "text-muted transition-transform",
                            active && "text-primary",
                            active && sort?.direction === "asc" && "rotate-180"
                          )}
                          aria-hidden="true"
                        />
                      </button>
                    ) : (
                      column.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/70">
            {loading
              ? Array.from({ length: 5 }, (_, index) => (
                  <tr key={`loading-${index}`} className={rowClassName}>
                    <td colSpan={columns.length} className={paddingClass}>
                      <div
                        className="h-5 animate-pulse rounded bg-muted/30 motion-reduce:animate-none"
                        aria-hidden="true"
                      />
                    </td>
                  </tr>
                ))
              : rows.length === 0
                ? (
                    <tr>
                      <td colSpan={columns.length} className="px-3 py-8">
                        {empty}
                      </td>
                    </tr>
                  )
                : rows.map((row, index) => {
                    const rowKey = getRowKey(row, index);
                    const selected = selectedRowKey != null && rowKey === selectedRowKey;
                    return (
                      <tr
                        key={rowKey}
                        data-selected={selected ? "true" : "false"}
                        aria-current={selected ? "true" : undefined}
                        aria-label={selectable ? getRowAriaLabel?.(row) : undefined}
                        className={cn(
                          "transition-colors",
                          selectable && "cursor-pointer",
                          selected ? "bg-primary/10" : selectable && "hover:bg-background",
                          rowClassName
                        )}
                        onClick={(event) => handleRowClick(event, row)}
                      >
                        {columns.map((column) => (
                          <td
                            key={column.key}
                            className={cn(
                              "align-top",
                              paddingClass,
                              alignClass(column.align as DataTableColumn<unknown>["align"]),
                              column.className
                            )}
                          >
                            {column.render
                              ? column.render(row, index)
                              : String((row as Record<string, unknown>)[column.key] ?? "")}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
