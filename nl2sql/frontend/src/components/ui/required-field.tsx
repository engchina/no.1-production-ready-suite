import { RequiredBadge } from "@engchina/production-ready-ui";
import { type ReactNode } from "react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// 必須表示は共有の RequiredBadge（中立色の「必須」テキストタグ）に統一する（#543 / platform #49）。
// TextField / SelectField で表せない入力（textarea・ファイル選択・複合入力・legend）のラベルだけをここで組む。

/**
 * 入力のラベル。必須のときは RequiredBadge を添える。
 * 入力側に required / aria-required を付けて必須を伝えるので、バッジは aria-hidden にして二重読み上げを避ける。
 */
export function FieldLabel({
  id,
  htmlFor,
  label,
  required = false,
  requiredLabel = t("common.required"),
  className,
  children,
}: {
  id?: string;
  htmlFor: string;
  label: ReactNode;
  required?: boolean;
  requiredLabel?: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <label id={id} htmlFor={htmlFor} className={cn("text-sm font-medium text-fg", className)}>
      {label}
      {required ? <RequiredBadge label={requiredLabel} aria-hidden className="ml-2 align-middle" /> : null}
      {children}
    </label>
  );
}

/** fieldset の見出し。legend には aria-required が無いので、バッジは読み上げ対象のままにする。 */
export function FieldLegend({
  id,
  children,
  required = false,
  requiredLabel = t("common.required"),
  className,
}: {
  id?: string;
  children: ReactNode;
  required?: boolean;
  requiredLabel?: string;
  className?: string;
}) {
  return (
    <legend id={id} className={cn("text-sm font-semibold text-fg", className)}>
      {children}
      {required ? <RequiredBadge label={requiredLabel} className="ml-2 align-middle" /> : null}
    </legend>
  );
}
