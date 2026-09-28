import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * 「回答エンジンが DocRAG のときは使われない」ことを欄の近くに示す補足(#300)。
 * 入力は残したまま、色だけに頼らずアイコンと文で伝える。`id` は欄の aria-describedby から参照する。
 */
export function DocragUnusedNote({
  id,
  children,
  className,
}: {
  id?: string;
  children: string;
  className?: string;
}) {
  return (
    <p
      id={id}
      data-testid="docrag-unused-note"
      className={cn("flex items-start gap-1.5 text-xs leading-relaxed text-fg-muted", className)}
    >
      <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
      <span>{children}</span>
    </p>
  );
}
