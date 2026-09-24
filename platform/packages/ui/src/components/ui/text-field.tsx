import { type InputHTMLAttributes, type ReactNode, type Ref, useId } from "react";

import { cn } from "../../lib/utils";

import { FieldError } from "./field-error";
import { RequiredBadge } from "./required-badge";

/** 入力欄の見た目（枠線は secondary ボタンと同じ --color-border-control）。 */
const fieldControlClass = cn(
  "w-full min-h-[var(--field-height)] rounded-md border bg-surface px-3 text-sm text-fg outline-none transition-colors",
  // プレースホルダも「文字」。透過で薄めると 3:1 を割るので fg-muted のまま使う
  "placeholder:text-fg-muted placeholder:opacity-100",
  "focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring",
  "disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
  "read-only:bg-surface-sunken",
  "forced-colors:border-[CanvasText]"
);

/**
 * ラベル・補足・エラー付きの 1 行入力。API は SelectField と揃える。
 * 必須はネイティブの required 検証にしない（未入力でも保存を許す画面があるため。SelectField と同じ）。
 * 値の扱いはネイティブ input と同じ（`value` / `onChange`）。文字列だけ欲しい場合は `onValueChange`。
 */
export function TextField({
  id,
  label,
  helper,
  error,
  required,
  requiredLabel,
  className,
  inputClassName,
  onValueChange,
  onChange,
  type = "text",
  ref,
  ...props
}: {
  id: string;
  /** 翻訳済みラベル。 */
  label: string;
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
  onValueChange?: (value: string) => void;
  ref?: Ref<HTMLInputElement>;
} & Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "required">) {
  const reactId = useId();
  const hintId = `${id}-${reactId}-hint`;
  const errorId = `${id}-${reactId}-error`;
  const describedBy = [helper ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;

  return (
    <div className={cn("space-y-1.5", className)}>
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-fg">
        {label}
        {required && requiredLabel ? (
          <RequiredBadge label={requiredLabel} aria-hidden />
        ) : null}
      </label>
      <input
        ref={ref}
        id={id}
        type={type}
        aria-required={required || undefined}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy}
        onChange={(event) => {
          onChange?.(event);
          onValueChange?.(event.target.value);
        }}
        className={cn(fieldControlClass, error ? "border-danger-fg" : "border-border-control", inputClassName)}
        {...props}
      />
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}
