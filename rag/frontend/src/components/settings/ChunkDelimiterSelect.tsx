import { SearchableMultiSelect } from "@production-ready/ui";

import { CHUNK_DELIMITER_OPTIONS } from "@/lib/chunking";
import { t } from "@/lib/i18n";

/** 分割符の複数選択（選んだ順が優先度の順。一覧に無い文字列も足せる。#4733）。 */
export function ChunkDelimiterSelect({
  id,
  value,
  onChange,
  disabled,
  error,
  required,
  labelHidden,
  helper,
}: {
  id: string;
  value: readonly string[];
  onChange: (value: string[]) => void;
  disabled?: boolean;
  error?: string;
  required?: boolean;
  labelHidden?: boolean;
  helper?: string;
}) {
  return (
    <SearchableMultiSelect
      id={id}
      className="col-span-full"
      width="lg"
      label={t("settings.chunking.params.delimiter")}
      labelHidden={labelHidden}
      helper={helper}
      error={error}
      required={required}
      requiredLabel={t("common.required")}
      options={CHUNK_DELIMITER_OPTIONS.map((option) => ({
        value: option.value,
        label: t(option.labelKey),
      }))}
      value={value}
      onValueChange={onChange}
      disabled={disabled}
      creatable
      labels={{
        searchPlaceholder: t("settings.chunking.params.delimiterPlaceholder"),
        create: (query) => t("settings.chunking.params.delimiterCreate", { value: query }),
      }}
    />
  );
}
