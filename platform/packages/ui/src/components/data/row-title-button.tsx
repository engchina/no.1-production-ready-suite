import {
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
  type Ref,
} from "react";

import { cn } from "../../lib/utils";
import { Tooltip } from "../ui/tooltip";

/** 長い題名を切り詰めるときの最大行数（既定は切り詰めずに折り返す）。 */
export type RowTitleButtonMaxLines = 1 | 2 | 3;

/**
 * 切り詰めた題名の Tooltip に出す最大文字数。Tooltip は短い文のための部品（README §4「`Tooltip`」）なので、
 * 長い内容（メモ・質問の全文など）は先頭だけを出し、全文は詳細で見せる。
 */
export const ROW_TITLE_TOOLTIP_MAX_CHARS = 120;

/** Tooltip の文言。長すぎるときは先頭 `ROW_TITLE_TOOLTIP_MAX_CHARS` 文字 + 「…」にする（サロゲートペアを割らない）。 */
export function rowTitleTooltipText(text: string, maxChars = ROW_TITLE_TOOLTIP_MAX_CHARS) {
  const chars = Array.from(text.trim());
  return chars.length > maxChars ? `${chars.slice(0, maxChars).join("")}…` : chars.join("");
}

const LINE_CLAMP_CLASS: Record<RowTitleButtonMaxLines, string> = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
};

export interface RowTitleButtonProps
  extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "title" | "children" | "type" | "aria-current"> {
  /** 対象名。文字列のほか、識別子の折り返しなど製品の表示部品も渡せる（色・太さは部品の既定にそろえる）。 */
  title: ReactNode;
  /** 対象名の下の補足（ID・日時・説明など）。`--color-fg-muted` の 12px。 */
  subtitle?: ReactNode;
  /**
   * 詳細に表示中の対象か。`aria-current="true"` を付ける（読み上げで「現在の項目」と伝える）。
   * 見た目の選択の状態は、行（`DataTable` の `selectedRowKey`）やカードが持つ。
   */
  current?: boolean;
  /**
   * 対象名を切り詰める最大行数。既定は切り詰めず、単語の途中でも折り返す。
   * 切り詰めたときは、ホバーとキーボードのフォーカスで全文の Tooltip を出す（読み上げは全文のまま。
   * 長い内容は先頭 `ROW_TITLE_TOOLTIP_MAX_CHARS` 文字まで）。
   */
  maxLines?: RowTitleButtonMaxLines;
  /** 切り詰めたときの Tooltip の全文。`title` が文字列なら省略できる。 */
  fullTitle?: string;
  ref?: Ref<HTMLButtonElement>;
}

function assignRef<T>(ref: Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value);
  else if (ref && typeof ref === "object") (ref as { current: T | null }).current = value;
}

/** 行数で切り詰めた要素が、実際に切り詰められているか（1px の丸め誤差は無視する）。 */
export function isClampedOverflow(element: Pick<HTMLElement, "scrollHeight" | "clientHeight">) {
  return element.scrollHeight - element.clientHeight > 1;
}

/**
 * 一覧の行の先頭セル（または一覧のカード）に置く対象名のボタン（#421）。
 * 行のクリックと同じ「選ぶ / 開く」をキーボード（Tab → Enter / Space）でも行えるようにする
 * （UX 契約 page-archetypes.md §0-7。選択だけの導線は RowActionMenu に入れない）。
 *
 * - 情報一覧の構造コントロールなので、アクションボタン（共有 Button）ではなく文字だけのボタンにする。
 *   ホバーで対象名に下線、押せる範囲は行全体（`DataTable` の `onRowClick`）とこのボタン。
 * - 選択中は `aria-current="true"`（APG: 詳細を切り替えるだけで押しても解除しないので `aria-pressed` ではなく、
 *   開閉する領域を持たないので `aria-expanded` でもない。`DataTable` の行の `aria-current` と同じ意味）。
 * - フォーカスはグローバルの `:focus-visible`（outline 2px）。タッチ端末では当たり判定を 44px 以上にする
 *   （`pr-touch-target`、#364。見た目の大きさは変えない）。
 */
export function RowTitleButton({
  title,
  subtitle,
  current = false,
  maxLines,
  fullTitle,
  className,
  ref,
  ...props
}: RowTitleButtonProps) {
  const titleRef = useRef<HTMLSpanElement | null>(null);
  const [clamped, setClamped] = useState(false);
  const fullText = maxLines ? (fullTitle ?? (typeof title === "string" ? title : undefined)) : undefined;
  const tooltipText = fullText ? rowTitleTooltipText(fullText) : undefined;

  // 切り詰めたときだけ全文の Tooltip を出す（幅が変わるたびに測り直す）。
  useLayoutEffect(() => {
    const element = titleRef.current;
    if (!tooltipText || !element) {
      setClamped(false);
      return undefined;
    }
    const measure = () => setClamped(isClampedOverflow(element));
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [tooltipText, maxLines]);

  const button = (
    <button
      {...props}
      ref={(node) => assignRef(ref, node)}
      type="button"
      aria-current={current ? "true" : undefined}
      data-row-title-button=""
      className={cn(
        "group/row-title pr-touch-target relative block min-w-0 max-w-full cursor-pointer rounded-sm text-left text-fg disabled:cursor-not-allowed disabled:text-fg-disabled",
        className
      )}
    >
      <span
        ref={titleRef}
        className={cn(
          "block break-words text-sm font-medium leading-5 underline-offset-2 [overflow-wrap:anywhere] group-enabled/row-title:group-hover/row-title:underline",
          maxLines && LINE_CLAMP_CLASS[maxLines]
        )}
      >
        {title}
      </span>
      {subtitle ? (
        <span className="mt-0.5 block break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">
          {subtitle}
        </span>
      ) : null}
    </button>
  );

  if (!tooltipText) return button;
  // 読み上げ名には全文が入っているので、吹き出しは説明として結び付けない（二重に読み上げない）。
  return (
    <Tooltip content={tooltipText} disabled={!clamped} describe={false}>
      {button}
    </Tooltip>
  );
}
