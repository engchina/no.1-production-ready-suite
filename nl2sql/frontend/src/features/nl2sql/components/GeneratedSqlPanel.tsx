import { Check, Copy, Play } from "lucide-react";
import { useState } from "react";

import { Button, StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import type { GeneratedSqlPanelData } from "../types";

/**
 * 生成 SQL のサマリ部（安全/エンジンバッジ・コピー/実行ボタン・SQL コード・説明文）。
 * タイムライン `generate_sql` ステップ内に埋め込めるよう Card に包まない。
 * `onExecute` が渡されたとき（＝プレビュー経路）だけ実行ボタンを出す。
 */
export function GeneratedSqlSummary({
  result,
  executeLoading = false,
  onExecute,
}: {
  result: GeneratedSqlPanelData;
  executeLoading?: boolean;
  onExecute?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const displayedSql = result.executable_sql || result.generated_sql;

  const copySql = async () => {
    await navigator.clipboard.writeText(displayedSql);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge
            variant={result.safety.is_safe ? "success" : "danger"}
            label={result.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
          />
          <span className="rounded-md bg-muted/30 px-2 py-1 text-xs text-foreground">
            {engineLabel(result.engine)}
          </span>
          {result.fallback_reason && (
            <span className="rounded-md bg-warning-bg px-2 py-1 text-xs text-warning">
              {t("nl2sql.result.fallback")}
            </span>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {onExecute && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={executeLoading}
              disabled={!result.safety.is_safe}
              onClick={onExecute}
            >
              <Play size={15} aria-hidden="true" />
              <span>{t("nl2sql.action.executePreview")}</span>
            </Button>
          )}
          <Button type="button" variant="secondary" size="sm" onClick={copySql}>
            {copied ? <Check size={15} aria-hidden="true" /> : <Copy size={15} aria-hidden="true" />}
            <span>{copied ? t("nl2sql.sql.copied") : t("nl2sql.sql.copy")}</span>
          </Button>
        </div>
      </div>
      <pre className="max-h-72 overflow-auto rounded-md border border-border bg-code p-4 text-sm leading-6 text-code-fg">
        <code>{displayedSql}</code>
      </pre>
      <p className="text-sm leading-6 text-foreground">{result.explanation}</p>
    </div>
  );
}
