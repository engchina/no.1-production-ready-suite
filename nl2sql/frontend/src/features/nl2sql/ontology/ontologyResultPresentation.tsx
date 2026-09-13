import type { ReactNode } from "react";
import { t } from "@/lib/i18n";

export function TechnicalDetails({
  value,
  children,
}: {
  value?: unknown;
  children?: ReactNode;
}) {
  return (
    <details className="min-w-0 rounded-md border border-border bg-surface-sunken p-3">
      <summary className="cursor-pointer text-sm font-medium">
        {t("ontologyUi.technicalDetails")}
      </summary>
      {children ?? (
        <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </details>
  );
}
export function fieldLabel(field: string) {
  const key = `ontologyResults.field.${field}` as Parameters<typeof t>[0];
  return t(key) === key ? field : t(key);
}
export function DefinitionValue({
  value,
  names = {},
}: {
  value: unknown;
  names?: Record<string, string>;
}) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length))
    return <span>{t("ontologyResults.unspecified")}</span>;
  if (typeof value === "boolean")
    return (
      <span>{t(value ? "ontologyResults.yes" : "ontologyResults.no")}</span>
    );
  if (Array.isArray(value))
    return (
      <ul className="grid gap-1">
        {value.map((item, index) => (
          <li key={index}>
            <DefinitionValue value={item} names={names} />
          </li>
        ))}
      </ul>
    );
  if (typeof value === "object")
    return (
      <dl className="grid gap-2">
        {Object.entries(value).map(([key, item]) => (
          <div key={key}>
            <dt className="font-medium">{fieldLabel(key)}</dt>
            <dd className="pl-3">
              <DefinitionValue value={item} names={names} />
            </dd>
          </div>
        ))}
      </dl>
    );
  return (
    <span className="break-words [overflow-wrap:anywhere]">
      {names[String(value)] ?? String(value)}
    </span>
  );
}
export function DefinitionFields({ definition }: { definition: Record<string, unknown> }) {
  return <dl className="grid min-w-0 gap-2 text-sm">{Object.entries(definition).filter(([key]) => !["id", "implementation_key", "origin", "review_status"].includes(key)).map(([key,value]) =>
    <div className="min-w-0 break-words" key={key}><dt className="font-medium">{fieldLabel(key)}</dt><dd><DefinitionValue value={key === "kind" && typeof value === "string" ? t(`ontologyResults.kind.${value}`) : value} /></dd></div>
  )}</dl>;
}
