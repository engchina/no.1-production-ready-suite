import {
  type FieldWidth,
  SearchField,
  SelectField,
} from "@engchina/production-ready-ui";
import { useId } from "react";

import { t } from "@/lib/i18n";

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
  /** grid の外に単独で置くときの幅（#613）。検索・所有者と並べるツールバーでは短い列挙の sm。 */
  width?: FieldWidth;
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
  width,
  className = "",
}: DbManagementSelectFieldProps<T>) {
  const id = useId();
  // 検索・所有者（SearchField）と同じ行に並べる選択欄。共有の SelectField で同じ高さ（md）・枠線・角丸にそろえる（#613 / #631）。
  return (
    <SelectField<T>
      id={`db-management-select-${id}`}
      label={label}
      value={value}
      options={options}
      onValueChange={onChange}
      disabled={disabled}
      width={width}
      className={`min-w-0 ${className}`}
    />
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
