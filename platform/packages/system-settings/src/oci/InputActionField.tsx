import type { ChangeEvent, InputHTMLAttributes, ReactNode } from "react";

import {
  Button,
  type ButtonProps,
  cn,
  FieldActionRow,
  FieldError,
  TextField,
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
  /** 翻訳済みのラベル（TextField の label）。 */
  label: string;
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
 * テキスト入力と右側の操作（取得・接続テスト）を 1 行に並べる欄（TextField + FieldActionRow）。
 * 高さは入力欄・ボタンとも md 36px（タッチ端末は 44px）。操作は入力欄の下端にそろい、375px では下に全幅（#613）。
 * 補足・エラーは FieldActionRow の footer に出す（欄の中に入れると操作の下端がずれるため）。#631 でネイティブの input から置き換えた。
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
    <FieldActionRow
      className={className}
      actions={
        // 入力と同じ行の操作なので、入力欄と同じ md（README §4「操作部品の高さと幅」）。
        <Button
          type={action.type ?? "button"}
          variant={action.variant ?? "secondary"}
          size="md"
          className={action.className}
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
      }
      footer={
        <>
          {helper ? (
            <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
              {helper}
            </p>
          ) : null}
          <FieldError id={errorId} message={error} />
          <FieldError id={actionErrorId} message={actionError} />
        </>
      }
    >
      <TextField
        id={id}
        label={label}
        type={type}
        value={value}
        readOnly={readOnly}
        disabled={disabled}
        required={required}
        requiredLabel={requiredLabel}
        aria-readonly={readOnly || undefined}
        // 補足・エラーは footer に出すので、TextField の helper / error ではなく aria で結び付ける。
        aria-invalid={Boolean(error)}
        aria-describedby={inputDescribedBy}
        autoComplete={autoComplete}
        placeholder={placeholder}
        data-testid={inputTestId}
        onChange={handleChange}
        inputClassName={cn(
          error && "border-danger-fg",
          readOnly && "cursor-default text-fg-muted",
          inputClassName
        )}
      />
    </FieldActionRow>
  );
}
