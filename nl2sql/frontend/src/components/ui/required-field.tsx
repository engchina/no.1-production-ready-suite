import { type ReactNode } from "react";

import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export function RequiredIndicator({
  label = t("common.required"),
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <>
      <span aria-hidden="true" className={cn("ml-0.5 text-danger", className)}>
        *
      </span>
      <span className="sr-only">{label}</span>
    </>
  );
}

export function FieldLabel({
  id,
  htmlFor,
  label,
  required = false,
  requiredLabel,
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
    <label id={id} htmlFor={htmlFor} className={cn("text-sm font-medium text-foreground", className)}>
      {label}
      {required ? <RequiredIndicator label={requiredLabel} /> : null}
      {children}
    </label>
  );
}

export function FieldLegend({
  id,
  children,
  required = false,
  requiredLabel,
  className,
}: {
  id?: string;
  children: ReactNode;
  required?: boolean;
  requiredLabel?: string;
  className?: string;
}) {
  return (
    <legend id={id} className={cn("text-sm font-semibold text-foreground", className)}>
      {children}
      {required ? <RequiredIndicator label={requiredLabel} /> : null}
    </legend>
  );
}

export function RequiredFieldsNote({
  className,
  label = t("common.required"),
}: {
  className?: string;
  label?: string;
}) {
  return (
    <p className={cn("text-xs leading-5 text-muted", className)}>
      <RequiredIndicator label={label} />
      <span className="ml-1">{t("common.requiredFieldsNote")}</span>
    </p>
  );
}
