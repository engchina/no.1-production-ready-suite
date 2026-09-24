import { cn } from "../../lib/utils";

/** ローディング用スケルトン（プレースホルダ）。 */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-border/60", className)}
      aria-hidden
    />
  );
}
