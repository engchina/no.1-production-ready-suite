import { t } from "@/lib/i18n";

const FIELD_LABELS: Record<string, string> = {
  raw_text: "OCR 原文",
  document_date: "日付",
  total_amount: "金額",
  summary: "要約",
  category: "分類",
};

function renderValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

/** VLM 抽出フィールドを key/value で表示する。 */
export function ExtractedFields({ fields }: { fields: Record<string, unknown> }) {
  const entries = Object.entries(fields);
  if (entries.length === 0) {
    return <p className="text-sm text-muted">{t("flow.extracted.empty")}</p>;
  }
  return (
    <dl className="divide-y divide-border">
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[120px_1fr] gap-3 py-2.5">
          <dt className="text-xs font-medium text-muted">{FIELD_LABELS[key] ?? key}</dt>
          <dd className="whitespace-pre-wrap break-words text-sm text-foreground">
            {renderValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
