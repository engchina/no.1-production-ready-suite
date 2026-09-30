/*
 * 操作部品（入力欄・選択欄・ボタン）の高さと、入力欄・選択欄の幅の段（#613）。
 * 規則は docs/design-system/README.md §4「操作部品の高さと幅」。
 *
 * - 高さは 3 段（sm 32px / md 36px / lg 40px、既定 md）。Button・TextField・SearchField・SecretField・
 *   SelectField・SearchableSelectField で同じ値（--control-height-*）を使い、同じ行の部品は同じ size にする。
 *   タッチ端末（pointer: coarse）では 3 段とも 44px（--control-height-touch）になるので、行の高さはそろったまま。
 * - 幅は値の長さで決める 5 段（xs 8rem / sm 12rem / md 20rem / lg 28rem / full）。sm（640px）未満は全幅。
 *   フォームの grid のセルに置く欄は指定しない（セルの幅いっぱい = full）。
 *
 * Tailwind が dist からクラス名を拾えるように、クラスは文字列のまま書く（組み立てない）。
 */
import { cn } from "../../lib/utils";

/** 操作部品の高さ。sm 32px（表・密な行）/ md 36px（既定）/ lg 40px（工程を進める主操作の行・主な問い合わせの入力の行）。 */
export type ControlSize = "sm" | "md" | "lg";

/**
 * 入力欄・選択欄の幅。入る値の長さで選ぶ（sm 未満の画面では全幅）。
 * - `xs` 8rem: 数値・件数・短いコード（「DEFAULT」・「10」）
 * - `sm` 12rem: 短い列挙（状態・種類・言語）
 * - `md` 20rem: 名前（ユーザー名・ロール名・表名）
 * - `lg` 28rem: 長めの名前（業務ビュー・ナレッジベース・モデル名）
 * - `full`: 親の幅いっぱい（URL・OCID・パス・文章・検索欄、フォームの grid のセル）
 */
export type FieldWidth = "xs" | "sm" | "md" | "lg" | "full";

/** 高さ（固定）。1 行で切り詰める選択欄のボタン（SelectField）が使う。 */
export const CONTROL_HEIGHT_CLASS: Record<ControlSize, string> = {
  sm: "h-[var(--control-height-sm)]",
  md: "h-[var(--control-height-md)]",
  lg: "h-[var(--control-height-lg)]",
};

/** 高さ（最小）。入力欄と、値を折り返す選択欄（SearchableSelectField）が使う。 */
export const CONTROL_MIN_HEIGHT_CLASS: Record<ControlSize, string> = {
  sm: "min-h-[var(--control-height-sm)]",
  md: "min-h-[var(--control-height-md)]",
  lg: "min-h-[var(--control-height-lg)]",
};

/**
 * 幅。sm（640px）未満は全幅、sm 以上は段の幅（親より広くはしない）。
 * 欄の外枠（ラベル・補足・エラーを含む）に付け、補足やエラーの文も欄の幅で折り返す。
 */
export const FIELD_WIDTH_CLASS: Record<FieldWidth, string> = {
  xs: "w-full sm:w-[var(--field-width-xs)] sm:max-w-full",
  sm: "w-full sm:w-[var(--field-width-sm)] sm:max-w-full",
  md: "w-full sm:w-[var(--field-width-md)] sm:max-w-full",
  lg: "w-full sm:w-[var(--field-width-lg)] sm:max-w-full",
  full: "w-full",
};

/** 幅の段のクラス。指定が無ければ何も付けない（外枠はブロック要素なので親の幅いっぱい）。 */
export function fieldWidthClass(width: FieldWidth | undefined) {
  return width ? FIELD_WIDTH_CLASS[width] : undefined;
}

/**
 * 入力欄の見た目（枠線は secondary ボタンと同じ --color-border-control、角丸は Button・SelectField と同じ --radius-control）。
 * TextField・TextareaField・SecretField と、ネイティブの `<select>` / `<input>`（`fieldControlClassName`）が共有する。
 */
export const fieldControlClass = cn(
  "w-full min-h-[var(--control-height-md)] rounded-control border bg-surface px-3 text-sm text-fg outline-none transition-colors",
  // プレースホルダも「文字」。透過で薄めると 3:1 を割るので fg-muted のまま使う
  "placeholder:text-fg-muted placeholder:opacity-100",
  "focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
  "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
  "read-only:bg-surface-sunken",
  "forced-colors:border-[CanvasText]",
  // type="search" のブラウザ既定のクリアボタン（Chromium / Safari の ×）と装飾を出さない。
  // キーボードで届かず読み上げ名も訳せないため、クリアは onClear の共有ボタン（または trailing）で出す（#384）。
  "[&::-webkit-search-cancel-button]:appearance-none [&::-webkit-search-decoration]:appearance-none"
);

export interface FieldControlClassNameOptions {
  /** 高さ（既定 md）。同じ行の Button と同じ size にする。 */
  size?: ControlSize;
  /** 幅（既定は親の幅いっぱい）。 */
  width?: FieldWidth;
  className?: string;
}

/**
 * ネイティブの `<select>` / `<input>` に、TextField・SelectField と同じ見た目・高さ・幅を付けるクラス（#613）。
 * 新しい画面は TextField / SelectField を使う。ネイティブの要素を残す画面（`<optgroup>` を使う選択・
 * ラベルを周りの部品が持つ欄など）だけがこれを使い、`h-*` / `w-*` / 枠線・角丸を手書きしない。
 * エラーは `aria-invalid="true"` を付ければ枠線が danger になる。
 */
export function fieldControlClassName({ size = "md", width, className }: FieldControlClassNameOptions = {}) {
  return cn(
    fieldControlClass,
    CONTROL_MIN_HEIGHT_CLASS[size],
    "border-border-control aria-[invalid=true]:border-danger-fg",
    // ネイティブの select は押せることを示す（入力欄は既定の I ビーム）。
    "[&:is(select)]:cursor-pointer",
    fieldWidthClass(width),
    className
  );
}
