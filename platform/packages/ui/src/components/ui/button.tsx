import { cva, type VariantProps } from "class-variance-authority";
import type { LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, Ref } from "react";

import { cn } from "../../lib/utils";
import { Spinner } from "./spinner";

/*
 * 枠線の方針（docs/design-system/README.md §6 罫線）:
 *   primary / danger  1px の透明枠線を残す（secondary と並べた時の 1px ズレ防止）
 *   secondary         --color-border-control（3:1 必須。入力欄と同じ線）
 *   ghost             枠なし（透明）
 * 高さ・余白・角丸は px トークン（ルート非依存）。
 */
export const buttonVariants = cva(
  [
    "inline-flex max-w-full min-w-[32px] cursor-pointer items-center justify-center gap-[var(--button-gap)] overflow-hidden whitespace-nowrap",
    "rounded-[var(--button-radius)] border border-transparent text-sm font-medium leading-5 transition-colors",
    "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring",
    "disabled:cursor-not-allowed disabled:border-border disabled:bg-surface-disabled disabled:text-fg-disabled disabled:shadow-none",
    "aria-pressed:border-accent-fg aria-pressed:bg-accent-muted aria-pressed:text-fg aria-pressed:shadow-[inset_0_0_0_1px_var(--color-accent-fg)]",
    "[&>span]:min-w-0 [&>span]:truncate [&>svg]:block [&>svg]:shrink-0",
    // 強制カラーモードでは塗りが消えるため、輪郭を必ず出す
    "forced-colors:border-[CanvasText]",
  ],
  {
    variants: {
      variant: {
        primary:
          "bg-accent-emphasis text-fg-on-accent hover:enabled:bg-accent-emphasis-hover active:enabled:bg-accent-emphasis-active forced-colors:bg-[Highlight] forced-colors:text-[HighlightText] forced-colors:forced-color-adjust-none",
        secondary:
          "border-border-control bg-surface text-fg hover:enabled:bg-surface-hover active:enabled:bg-[color-mix(in_srgb,var(--color-fg)_12%,var(--color-surface))]",
        ghost:
          "bg-transparent text-fg hover:enabled:bg-surface-hover active:enabled:bg-[color-mix(in_srgb,var(--color-fg)_12%,var(--color-surface))]",
        danger:
          "bg-danger-emphasis text-fg-on-emphasis hover:enabled:bg-[color-mix(in_srgb,var(--color-danger-emphasis)_88%,#000)] active:enabled:bg-[color-mix(in_srgb,var(--color-danger-emphasis)_76%,#000)] forced-colors:bg-[Highlight] forced-colors:text-[HighlightText] forced-colors:forced-color-adjust-none",
      },
      size: {
        sm: "h-[var(--button-height-sm)] min-h-[var(--button-height-sm)] px-[var(--button-padding-sm)]",
        md: "h-[var(--button-height-md)] min-h-[var(--button-height-md)] px-[var(--button-padding-md)]",
        lg: "h-[var(--button-height-lg)] min-h-[var(--button-height-lg)] px-[var(--button-padding-lg)]",
      },
      iconOnly: { true: "aspect-square min-w-0 px-0", false: "" },
      touchTarget: { true: "h-[var(--control-height-touch)] min-h-[var(--control-height-touch)]", false: "" },
      /** secondary / ghost を破壊的な操作に使うときの文字色（塗りの danger より控えめ）。 */
      tone: {
        default: "",
        danger: "text-danger-fg hover:enabled:bg-danger-subtle hover:enabled:text-danger-fg",
      },
    },
    compoundVariants: [{ variant: "secondary", tone: "danger", className: "border-danger-fg" }],
    defaultVariants: { variant: "primary", size: "md", iconOnly: false, touchTarget: false, tone: "default" },
  }
);

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    Omit<VariantProps<typeof buttonVariants>, "iconOnly" | "touchTarget"> {
  /** 先頭アイコン（lucide-react のコンポーネント。例: `icon={Upload}`）。子要素にアイコンを直接書かない。 */
  icon?: LucideIcon;
  /** 方向・開閉・外部リンクのみ（ChevronRight / ChevronDown / ExternalLink）。アイコンを 2 つ持たせない。 */
  trailingIcon?: LucideIcon;
  /** アイコンだけのボタン。`aria-label` を必ず付ける。 */
  iconOnly?: boolean;
  /** マウス環境でも 44px の高さにする場合だけ true（タッチ端末では --button-height-* が 44px になる）。 */
  touchTarget?: boolean;
  /** true で先頭アイコンがスピナーに置き換わる（ラベル・幅は変わらない）。`aria-busy` と `disabled` が付く。 */
  loading?: boolean;
  /** トグルボタンとして使う場合の押下状態（`aria-pressed`）。 */
  pressed?: boolean;
  ref?: Ref<HTMLButtonElement>;
}

/**
 * RAG / NL2SQL / Agent 共通のボタン。4 バリアント × 3 サイズ（32 / 36 / 40px）。
 *
 * 非同期の操作を起こすボタンは必ず `icon` を持たせる。loading 中は先頭アイコンがスピナーに
 * 置き換わるため幅が変わらない（アイコンが無いとスピナーの分だけ幅が広がる）。
 * ラベルは「実行中…」等に差し替えない。
 * `type` の既定値はブラウザ既定（フォーム内では submit）のまま。既存フォームの暗黙 submit を壊さないため。
 */
export function Button({
  className,
  variant,
  size,
  tone,
  icon: Icon,
  trailingIcon: TrailingIcon,
  iconOnly = false,
  touchTarget = false,
  loading,
  pressed,
  disabled,
  children,
  ref,
  ...props
}: ButtonProps) {
  return (
    <button
      ref={ref}
      className={cn(
        buttonVariants({ variant, size, tone, iconOnly, touchTarget }),
        // 旧スタイル（子に直接アイコンを書く）互換: loading 中は子の svg を隠してスピナーと二重にしない
        loading && "[&>svg:not(.animate-spin)]:hidden",
        className
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      aria-pressed={pressed}
      {...props}
    >
      {/* 先頭スロットは常に 16px。スピナーは全周トラック付き（回転してもシルエットが変わらない）。 */}
      {loading ? <Spinner size={16} /> : Icon ? <Icon size={16} aria-hidden /> : null}
      {children}
      {TrailingIcon && !loading ? <TrailingIcon size={16} aria-hidden /> : null}
    </button>
  );
}
