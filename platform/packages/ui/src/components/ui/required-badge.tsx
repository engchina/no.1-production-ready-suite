import { cn } from "../../lib/utils";

/**
 * 入力項目が必須であることを示すテキストのタグ（例:「必須」「OCI 運用時必須」）。
 *
 * - 必須は「状態」ではなく項目の「属性（情報）」なので、状態色（warning / danger）を使わず中立色で出す。
 *   状態色は未入力エラーなど利用者の対応が要るときの FieldError に取っておく。
 * - 記号（`*`）ではなくテキストにする。凡例（「* は必須」）が要らず、色にも頼らない（WCAG 1.4.1 / 3.3.2）。
 * - 地は塗らず、置かれた面の色を透過する。文字は --color-fg-muted（surface 上 4.83:1、sunken 上 4.55:1、ダーク 7:1 以上）。
 *
 * TextField / SelectField は `required` + `requiredLabel` で内部的にこれを出す。
 * それらで表せない入力（ファイル選択・fieldset の legend・複合入力）のラベルに置くときに直接使う。
 * 既定では読み上げ対象に含む。入力側に aria-required / required を付けて必須を伝えている場合は
 * `aria-hidden` を渡して二重読み上げを避ける。
 */
export function RequiredBadge({
  label,
  className,
  "aria-hidden": ariaHidden,
}: {
  /** 翻訳済みの文言（例:「必須」）。 */
  label: string;
  className?: string;
  "aria-hidden"?: boolean;
}) {
  return (
    <span
      aria-hidden={ariaHidden || undefined}
      className={cn(
        "inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium text-fg-muted",
        // 輪郭は装飾（意味は文字が持つ）。ring-inset なので行の高さを変えない
        "ring-1 ring-inset ring-border-strong",
        className
      )}
    >
      {label}
    </span>
  );
}
