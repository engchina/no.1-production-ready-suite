import { SearchField, fieldControlClassName } from "@engchina/production-ready-ui";
import { useId } from "react";

import { t } from "@/lib/i18n";

// 種類の select（native）の見た目。検索・所有者は共有の SearchField（#535）で作り、同じ行に並べる select も
// 共有の fieldControlClassName で同じ高さ（md。タッチ端末は 44px）・枠線・角丸にそろえる（#384 / #613）。
const INPUT_CLASS = fieldControlClassName({ className: "py-2" });

export interface DbObjectFilterFieldProps {
  label: string;
  placeholder: string;
  /** 適用中の値。 */
  value: string;
  /** 値が確定したとき（入力が止まって 300ms・Enter・消去。IME の変換中は呼ばない。#535）。 */
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
  // 一覧の絞り込みは共有の SearchField（debounce・IME 対応・消去）で作る（#535）。
  return (
    <SearchField
      id={`db-object-search-${id}`}
      label={label}
      value={value}
      disabled={disabled}
      onSearch={onChange}
      clearLabel={t("common.clearSearch")}
      placeholder={placeholder}
      autoComplete="off"
      className={`min-w-0 ${className}`}
    />
  );
}

const toUpperCase = (value: string) => value.toUpperCase();

/** 所有者の接頭辞の正規化（前後の空白を落として大文字にする）。 */
export const normalizeOwnerPrefix = (value: string) => value.trim().toUpperCase();

export function DbOwnerPrefixFilterField({
  label,
  placeholder,
  value,
  onChange,
  disabled = false,
  className = "",
}: DbObjectFilterFieldProps) {
  const id = useId();
  // 所有者の接頭辞も一覧の絞り込み。入力中の文字を大文字で見せ、確定した値も大文字にして渡す（Oracle の所有者名は大文字）。
  return (
    <SearchField
      id={`db-owner-prefix-${id}`}
      label={label}
      value={value}
      disabled={disabled}
      onSearch={onChange}
      normalize={normalizeOwnerPrefix}
      formatInput={toUpperCase}
      clearLabel={t("common.clearInput")}
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
