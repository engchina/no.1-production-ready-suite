import { Search } from "lucide-react";

const INPUT_CLASS =
  "min-h-11 w-full rounded-md border border-border bg-card px-3 py-2 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/20 disabled:text-muted";

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

export function DbManagementSearchField({
  label,
  placeholder,
  value,
  onChange,
  disabled = false,
  className = "",
}: DbObjectFilterFieldProps) {
  return (
    <label className={`grid min-w-0 gap-1 text-sm font-medium text-foreground ${className}`}>
      <span>{label}</span>
      <span className="relative">
        <Search
          size={16}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted"
          aria-hidden="true"
        />
        <input
          type="search"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.currentTarget.value)}
          className={`${INPUT_CLASS} pl-9`}
          placeholder={placeholder}
          autoComplete="off"
        />
      </span>
    </label>
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
  return (
    <label className={`grid min-w-0 gap-1 text-sm font-medium text-foreground ${className}`}>
      <span>{label}</span>
      <input
        type="search"
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.value.toUpperCase())}
        className={INPUT_CLASS}
        placeholder={placeholder}
        autoCapitalize="characters"
        autoComplete="off"
        spellCheck={false}
      />
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
