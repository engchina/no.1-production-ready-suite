import type { ChangeEvent, InputHTMLAttributes, ReactNode } from "react";

import { cn } from "@engchina/production-ready-ui";

import {
  Button,
  type ButtonProps,
  FieldError,
  FieldLabel,
  fieldControlClassName,
} from "@engchina/production-ready-ui";

export interface InputActionFieldAction {
  label: ReactNode;
  ariaLabel?: string;
  icon?: ButtonProps["icon"];
  type?: ButtonProps["type"];
  variant?: ButtonProps["variant"];
  loading?: boolean;
  disabled?: boolean;
  onClick?: ButtonProps["onClick"];
  dataTestId?: string;
  className?: string;
}

export interface InputActionFieldProps {
  id: string;
  label: ReactNode;
  value: string;
  onChange?: (value: string) => void;
  placeholder?: string;
  helper?: string;
  error?: string;
  actionError?: string;
  required?: boolean;
  requiredLabel?: string;
  readOnly?: boolean;
  disabled?: boolean;
  type?: InputHTMLAttributes<HTMLInputElement>["type"];
  autoComplete?: string;
  className?: string;
  inputClassName?: string;
  inputTestId?: string;
  action: InputActionFieldAction;
}

/**
 * テキスト入力と右側の操作（取得・接続テスト）を同じ高さ（md 36px、タッチ端末は 44px）でそろえる欄。
 * 以前はマウス環境でも 44px（touchTarget）にしていたが、フォームのほかの欄（36px）とずれるためやめた（#613）。
 */
export function InputActionField({
  id,
  label,
  value,
  onChange,
  placeholder,
  helper,
  error,
  actionError,
  required,
  requiredLabel,
  readOnly = false,
  disabled = false,
  type = "text",
  autoComplete,
  className,
  inputClassName,
  inputTestId,
  action,
}: InputActionFieldProps) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const actionErrorId = `${id}-action-error`;
  const inputDescribedBy =
    [helper ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    if (readOnly || disabled) return;
    onChange?.(event.currentTarget.value);
  }

  return (
    <div className={cn("space-y-1.5", className)}>
      <FieldLabel
        htmlFor={id}
        label={label}
        required={required}
        requiredLabel={requiredLabel}
      />
      <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <input
          id={id}
          type={type}
          value={value}
          readOnly={readOnly}
          disabled={disabled}
          required={required}
          aria-readonly={readOnly || undefined}
          aria-required={required}
          aria-invalid={Boolean(error)}
          aria-describedby={inputDescribedBy}
          autoComplete={autoComplete}
          placeholder={placeholder}
          data-testid={inputTestId}
          onChange={handleChange}
          className={fieldControlClassName({
            className: cn(readOnly && "cursor-default text-fg-muted", inputClassName),
          })}
        />
        {/* 入力と同じ行の操作なので、入力欄と同じ md（README §4「操作部品の高さと幅」）。 */}
        <Button
          type={action.type ?? "button"}
          variant={action.variant ?? "secondary"}
          size="md"
          className={cn("w-full", action.className)}
          aria-label={action.ariaLabel}
          aria-describedby={actionError ? actionErrorId : undefined}
          icon={action.icon}
          loading={action.loading}
          disabled={disabled || action.disabled}
          data-testid={action.dataTestId}
          onClick={action.onClick}
        >
          <span>{action.label}</span>
        </Button>
      </div>
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
      <FieldError id={actionErrorId} message={actionError} />
    </div>
  );
}
