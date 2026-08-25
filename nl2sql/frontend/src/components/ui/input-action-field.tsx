import type { ChangeEvent, InputHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

import { Button, type ButtonProps } from "./button";
import { FieldError } from "./field-error";
import { FieldLabel } from "./required-field";

export interface InputActionFieldAction {
  label: ReactNode;
  ariaLabel?: string;
  icon?: ReactNode;
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

/** テキスト入力と右側アクションを同じ 44px 高で揃える共通フィールド。 */
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
          className={cn(
            "h-11 w-full min-h-[44px] rounded-md border px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
            readOnly ? "cursor-default bg-background text-muted" : "bg-card",
            disabled && "cursor-not-allowed opacity-60",
            error ? "border-danger" : "border-border",
            inputClassName
          )}
        />
        {/* 入力と同じ行の操作なので、Button spec の許容例に従い入力高 44px に揃える。 */}
        <Button
          type={action.type ?? "button"}
          variant={action.variant ?? "secondary"}
          size="lg"
          className={cn("h-11 w-full whitespace-nowrap min-h-[44px]", action.className)}
          aria-label={action.ariaLabel}
          aria-describedby={actionError ? actionErrorId : undefined}
          loading={action.loading}
          disabled={disabled || action.disabled}
          data-testid={action.dataTestId}
          onClick={action.onClick}
        >
          {action.loading ? null : action.icon}
          <span>{action.label}</span>
        </Button>
      </div>
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
      <FieldError id={actionErrorId} message={actionError} />
    </div>
  );
}
