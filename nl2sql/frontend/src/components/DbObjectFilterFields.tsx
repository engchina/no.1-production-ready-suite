import { TextField } from "@engchina/production-ready-ui";
import { Search } from "lucide-react";
import { useId } from "react";

import { t } from "@/lib/i18n";

// 種類の select（native）の見た目。検索・所有者は共有の TextField（touchTarget = 44px）で作り、
// 同じ行に並べる select も同じ高さ・枠線（--color-border-control）・角丸（--radius-control）にそろえる（#384）。
const INPUT_CLASS =
  "min-h-[44px] w-full rounded-control border border-border-control bg-surface px-3 py-2 focus:border-focus-ring disabled:cursor-not-allowed disabled:bg-surface-hover disabled:text-fg-disabled";

export interface DbObjectFilterFieldProps {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

export interface DbObjectSearchOwnerFieldsProps {
  searchLabel: string;
  searchPlaceholder: string;
  searchValue: string;
  onSearchChange: (value: string) => void;
  ownerLabel: string;
  ownerPlaceholder: string;
  ownerValue: string;
  onOwnerChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
}

export interface DbManagementSelectOption<T extends string = string> {
  value: T;
  label: string;
}

export interface DbManagementSelectFieldProps<T extends string = string> {
  label: string;
  value: T;
  options: readonly DbManagementSelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}

export function DbManagementSearchField({
  label,
  placeholder,
  value,
  onChange,
  disabled = false,
  className = "",
}: DbObjectFilterFieldProps) {
  const id = useId();
  return (
    <TextField
      id={`db-object-search-${id}`}
      label={label}
      type="search"
      value={value}
      disabled={disabled}
      onValueChange={onChange}
      onClear={() => onChange("")}
      clearLabel={t("common.clearSearch")}
      leadingIcon={Search}
      touchTarget
      placeholder={placeholder}
      autoComplete="off"
      className={`min-w-0 ${className}`}
    />
  );
}

export function DbOwnerPrefixFilterField({
  label,
  placeholder,
  value,
  onChange,
  disabled = false,
  className = "",
}: DbObjectFilterFieldProps) {
  const id = useId();
  return (
    <TextField
      id={`db-owner-prefix-${id}`}
      label={label}
      type="search"
      value={value}
      disabled={disabled}
      onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
      onClear={() => onChange("")}
      clearLabel={t("common.clearInput")}
      touchTarget
      placeholder={placeholder}
      autoCapitalize="characters"
      autoComplete="off"
      spellCheck={false}
      className={`min-w-0 ${className}`}
    />
  );
}

export function DbManagementSelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled = false,
  className = "",
}: DbManagementSelectFieldProps<T>) {
  return (
    // ラベルと入力欄の間隔は TextField（space-y-1.5）と同じにし、同じ行の入力欄の上端をそろえる。
    <label className={`grid min-w-0 gap-1.5 text-sm font-medium text-fg ${className}`}>
      <span>{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value as T)}
        className={INPUT_CLASS}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function DbObjectSearchOwnerFields({
  searchLabel,
  searchPlaceholder,
  searchValue,
  onSearchChange,
  ownerLabel,
  ownerPlaceholder,
  ownerValue,
  onOwnerChange,
  disabled = false,
  className = "",
}: DbObjectSearchOwnerFieldsProps) {
  return (
    <div className={`grid min-w-0 gap-2 md:grid-cols-2 ${className}`}>
      <DbManagementSearchField
        label={searchLabel}
        placeholder={searchPlaceholder}
        value={searchValue}
        disabled={disabled}
        onChange={onSearchChange}
      />
      <DbOwnerPrefixFilterField
        label={ownerLabel}
        placeholder={ownerPlaceholder}
        value={ownerValue}
        disabled={disabled}
        onChange={onOwnerChange}
      />
    </div>
  );
}
