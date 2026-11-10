import type { HTMLAttributes } from "react";

import { cn } from "../../lib/utils";

/** カード（shadcn/ui 流のプリミティブ）。 */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-surface shadow-[var(--shadow-card)]",
        className
      )}
      {...props}
    />
  );
}

export function CardHeader({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 p-5 pb-3", className)} {...props} />;
}

export function CardTitle({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return (
    // min-w-0 と overflow-wrap:anywhere: 改行の機会の無い長い名前でもカードを押し広げず、横の要素に重ならない（#4801）。
    <h2 className={cn("min-w-0 text-sm font-semibold text-fg [overflow-wrap:anywhere]", className)} {...props} />
  );
}

export function CardDescription({
  className,
  ...props
}: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("min-w-0 text-xs text-fg-muted [overflow-wrap:anywhere]", className)} {...props} />;
}

export function CardContent({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5 pt-0", className)} {...props} />;
}
