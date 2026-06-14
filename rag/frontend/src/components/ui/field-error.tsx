import { cn } from "@/lib/utils";

/**
 * FieldError（フィールド検証エラー）。docs/frontend-messaging-spec.md §3.2。
 * 該当入力欄の直下に置き、入力側の `aria-describedby={id}` と対応させる。
 * `message` が空のときは何も描画しない。
 */
export function FieldError({
  id,
  message,
  className,
}: {
  id: string;
  message?: string | null;
  className?: string;
}) {
  if (!message) return null;
  return (
    <p id={id} role="alert" className={cn("text-xs text-danger", className)}>
      {message}
    </p>
  );
}
