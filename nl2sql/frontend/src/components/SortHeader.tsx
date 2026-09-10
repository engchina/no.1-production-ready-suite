import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";
import "./SortHeader.css";

/** 列頭専用の並べ替え操作。ネイティブのキーボード操作を保ち、Action Button の外観は使わない。 */
export function SortHeader({ className, ...props }: ComponentProps<"button">) {
  return <button {...props} type="button" data-sort-header="true" className={cn("nl2sql-sort-header", className)} />;
}
