import { type LucideIcon, X } from "lucide-react";
import {
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type Ref,
  useCallback,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { cn } from "../../lib/utils";

import { Button } from "./button";
import { FieldError } from "./field-error";
import { RequiredBadge } from "./required-badge";

/**
 * 入力欄の見た目（枠線は secondary ボタンと同じ --color-border-control、角丸は Button・SelectField と同じ --radius-control）。
 * SecretField も同じ見た目にするため、パッケージ内でだけ共有する（index.ts からは export しない）。
 */
export const fieldControlClass = cn(
  "w-full min-h-[var(--field-height)] rounded-control border bg-surface px-3 text-sm text-fg outline-none transition-colors",
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

/** 高さ。md は既定の --field-height（36px）、lg は隣に置く lg の Button と同じ --button-height-lg（40px、タッチ端末は 44px）。 */
export type TextFieldSize = "md" | "lg";

const sizeClass: Record<TextFieldSize, string> = {
  md: "",
  lg: "min-h-[var(--button-height-lg)]",
};

/**
 * 先頭アイコンがあるときの左の余白 = アイコンの左の位置（px-3）+ アイコン（16px）+ Button のアイコンと文字の間隔（8px）。
 * 文字の開始位置を、アイコン付きの Button と同じ間隔にそろえる。
 */
export const TEXT_FIELD_LEADING_PADDING_CLASS = "pl-[calc(var(--space-3)+var(--icon-md)+var(--button-gap))]";
/** 後置スロットの幅を測る前（SSR・初回の描画）の右の余白。四角の iconOnly ボタン 1 つ分（入力欄の高さ）。 */
export const TEXT_FIELD_TRAILING_FALLBACK_PADDING_CLASS = "pr-[var(--field-height)]";

/** 制御された value が空でないか（クリアボタンを出すか）。 */
export function hasTextValue(value: unknown) {
  if (value == null) return false;
  if (Array.isArray(value)) return value.length > 0;
  return String(value) !== "";
}

/**
 * Escape で入力を消すか。消す値があり、onClear があるときだけ（空のときは囲むダイアログ等の Escape に任せる）。
 * IME の変換中（isComposing）は変換の取り消しなので消さない。
 */
export function shouldClearOnEscape({
  key,
  value,
  hasClear,
  isComposing = false,
}: {
  key: string;
  value: unknown;
  hasClear: boolean;
  isComposing?: boolean;
}) {
  return hasClear && key === "Escape" && !isComposing && hasTextValue(value);
}

/**
 * 入力を消して入力欄にフォーカスを戻す。クリアボタンは値が空になると消えるため、
 * フォーカスを戻さないと body へ外れてキーボードの利用者が位置を失う。
 */
export function clearTextField(onClear: () => void, input: Pick<HTMLInputElement, "focus"> | null) {
  onClear();
  input?.focus();
}

export type TextFieldProps = {
  id: string;
  /** 翻訳済みラベル。 */
  label: string;
  /**
   * ラベルを画面に出さず読み上げだけにする（`sr-only`）。検索欄のように、アイコンとプレースホルダで目的が分かる場所だけに使う。
   * フォームの入力欄では使わない（プレースホルダをラベルの代わりにしない）。
   */
  labelHidden?: boolean;
  /** 翻訳済みの補足（任意）。ドキュメントへのリンクなどを含めてよい。 */
  helper?: ReactNode;
  /** 翻訳済みのエラー（任意）。指定時は aria-invalid と枠線の色が変わる。 */
  error?: string;
  /** 必須であることを aria-required と中立色の RequiredBadge で伝える。ネイティブの required 検証は行わない（検証はアプリ側）。 */
  required?: boolean;
  /** 必須バッジの文言（例:「必須」）。required のときは必ず渡す（無いと見た目で必須が分からない）。 */
  requiredLabel?: string;
  className?: string;
  inputClassName?: string;
  /** 高さ（既定 md = --field-height の 36px）。lg の Button と同じ行に並べる入力欄だけ lg（--button-height-lg）。 */
  size?: TextFieldSize;
  /**
   * マウス環境でも 44px（--control-height-touch）にする場合だけ true。Button の `touchTarget` と同じ。
   * 44px の Button・select と同じ行に並べる入力欄に使う（size より優先）。
   */
  touchTarget?: boolean;
  /** 先頭のアイコン（lucide-react のコンポーネント。例: `leadingIcon={Search}`）。16px・読み上げない。 */
  leadingIcon?: LucideIcon;
  /** 末尾の任意の要素（単位・件数・ボタン）。値を消すボタンは `onClear` を使う。 */
  trailing?: ReactNode;
  /** 値を消す操作。渡すと、値があるときだけ末尾にクリアボタンを出し、Escape でも消す。`clearLabel` と一緒に渡す。 */
  onClear?: () => void;
  /** クリアボタンの読み上げ名と Tooltip（翻訳済み。例:「検索語をクリア」）。 */
  clearLabel?: string;
  onValueChange?: (value: string) => void;
  ref?: Ref<HTMLInputElement>;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "required" | "size">;

/**
 * ラベル・補足・エラー付きの 1 行入力。API は SelectField と揃える。
 * 必須はネイティブの required 検証にしない（未入力でも保存を許す画面があるため。SelectField と同じ）。
 * 値の扱いはネイティブ input と同じ（`value` / `onChange`）。文字列だけ欲しい場合は `onValueChange`。
 *
 * 検索欄などの「アイコン付きの入力欄」もこれで作る（#384。製品で `relative` + 絶対配置のアイコン + `pl-9` を手書きしない）。
 * - `leadingIcon`: 先頭の 16px のアイコン。読み上げず、ポインタを透過する（押すと下の入力欄にフォーカスが入る）。
 * - `onClear` + `clearLabel`: 値があるときだけ末尾に「クリア」の iconOnly ボタンを出す（入力欄の直後の Tab 順。
 *   押すと値を消して入力欄に戻る）。Escape でも消える（値があるときだけ。空なら Escape は囲むダイアログ等に渡す）。
 *   value を制御して使う。
 * - `trailing`: 末尾の任意の要素（単位・件数・ボタンなど）。枠線の内側の右端に置き、実際の幅の分だけ文字の右の余白を空ける。
 */
export function TextField({
  id,
  label,
  labelHidden = false,
  helper,
  error,
  required,
  requiredLabel,
  className,
  inputClassName,
  size = "md",
  touchTarget = false,
  leadingIcon: LeadingIcon,
  trailing,
  onClear,
  clearLabel,
  onValueChange,
  onChange,
  onKeyDown,
  type = "text",
  ref,
  ...props
}: TextFieldProps) {
  const reactId = useId();
  const hintId = `${id}-${reactId}-hint`;
  const errorId = `${id}-${reactId}-error`;
  const describedBy =
    [helper ? hintId : "", error ? errorId : "", props["aria-describedby"] ?? ""].filter(Boolean).join(" ") ||
    undefined;

  const inputRef = useRef<HTMLInputElement | null>(null);
  const setInputRef = useCallback(
    (node: HTMLInputElement | null) => {
      inputRef.current = node;
      if (typeof ref === "function") ref(node);
      else if (ref) ref.current = node;
    },
    [ref]
  );

  const showClear = Boolean(onClear) && !props.disabled && !props.readOnly && hasTextValue(props.value);
  const hasTrailing = showClear || (trailing !== undefined && trailing !== null && trailing !== false);

  // 後置スロットの実際の幅だけ文字の右の余白を空ける（件数・単位など幅が決まらない要素も入れられるように）。
  const trailingRef = useRef<HTMLDivElement | null>(null);
  const [trailingWidth, setTrailingWidth] = useState(0);
  useLayoutEffect(() => {
    const node = trailingRef.current;
    if (!hasTrailing || !node) {
      setTrailingWidth(0);
      return undefined;
    }
    const measure = () => setTrailingWidth(Math.ceil(node.getBoundingClientRect().width));
    measure();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasTrailing]);

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    onKeyDown?.(event);
    if (event.defaultPrevented || !onClear) return;
    if (
      shouldClearOnEscape({
        key: event.key,
        value: props.value,
        hasClear: true,
        isComposing: event.nativeEvent.isComposing,
      })
    ) {
      // 囲むダイアログ・メニューを閉じない（1 回目の Escape は入力を消すだけ）。
      event.preventDefault();
      event.stopPropagation();
      onClear();
    }
  }

  return (
    <div className={cn("space-y-1.5", className)}>
      <label
        htmlFor={id}
        className={cn("flex items-center gap-2 text-sm font-medium text-fg", labelHidden && "sr-only")}
      >
        {label}
        {required && requiredLabel ? <RequiredBadge label={requiredLabel} aria-hidden /> : null}
      </label>
      <div className="relative">
        <input
          ref={setInputRef}
          id={id}
          type={type}
          aria-required={required || undefined}
          aria-invalid={Boolean(error)}
          {...props}
          aria-describedby={describedBy}
          onChange={(event) => {
            onChange?.(event);
            onValueChange?.(event.target.value);
          }}
          onKeyDown={handleKeyDown}
          className={cn(
            fieldControlClass,
            // 先頭アイコンの色を disabled に合わせるため peer にする（アイコンは入力欄の後ろに置く）。
            "peer",
            touchTarget ? "min-h-[var(--control-height-touch)]" : sizeClass[size],
            LeadingIcon && TEXT_FIELD_LEADING_PADDING_CLASS,
            hasTrailing && TEXT_FIELD_TRAILING_FALLBACK_PADDING_CLASS,
            error ? "border-danger-fg" : "border-border-control",
            inputClassName
          )}
          style={hasTrailing && trailingWidth > 0 ? { ...props.style, paddingRight: trailingWidth } : props.style}
        />
        {LeadingIcon ? (
          <LeadingIcon
            size={16}
            aria-hidden
            data-text-field-slot="leading"
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted peer-disabled:text-fg-disabled"
          />
        ) : null}
        {hasTrailing ? (
          <div
            ref={trailingRef}
            data-text-field-slot="trailing"
            // 枠線の内側に置く（ボタンのホバーの地が入力欄の枠線に重ならないように）。
            className="absolute inset-y-px right-px flex items-center"
          >
            {trailing}
            {showClear ? (
              <Button
                type="button"
                variant="ghost"
                iconOnly
                icon={X}
                aria-label={clearLabel}
                aria-controls={id}
                // 押しても入力欄からフォーカスを外さない（blur で確定する検索欄が、消す前の値を確定しないように）。
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => clearTextField(onClear as () => void, inputRef.current)}
                // 入力欄の高さに合わせた正方形。外側の角だけ入力欄の角丸（枠線の内側なので 1px 小さく）に合わせる。
                // 強制カラーモードでは Button が輪郭を出すが、入力欄の枠線と二重にならないよう左の区切りだけ残す
                // （透明の枠線は CanvasText に塗られるため、背景と同じ Canvas にする）。
                className="h-full min-h-0 rounded-l-none rounded-r-[calc(var(--radius-control)-1px)] forced-colors:border-y-[Canvas] forced-colors:border-r-[Canvas]"
              />
            ) : null}
          </div>
        ) : null}
      </div>
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}
