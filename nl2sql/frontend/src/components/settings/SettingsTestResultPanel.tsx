import { Banner, MessageText } from "@engchina/production-ready-ui";

import { StatusBadge } from "@/components/ui/status-badge";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type SettingsTestResultTone = "success" | "warning" | "danger";
export type SettingsTestResultValue = string | number | boolean;

export interface SettingsTestResultDetail {
  label: string;
  value: SettingsTestResultValue;
}

export interface SettingsTestResultPanelProps {
  tone: SettingsTestResultTone;
  message: string;
  elapsedMs?: number;
  checkedAt?: string;
  details?: readonly SettingsTestResultDetail[];
  troubleshooting?: readonly string[];
  errorType?: string | null;
  rawError?: string | null;
  className?: string;
  testId?: string;
}

/** システム設定の接続・モデルテスト結果を同じ情報階層で表示する。 */
export function SettingsTestResultPanel({
  tone,
  message,
  elapsedMs,
  checkedAt,
  details = [],
  troubleshooting = [],
  errorType,
  className,
  testId,
}: SettingsTestResultPanelProps) {
  const hasTiming = elapsedMs !== undefined || Boolean(checkedAt);

  return (
    <div
      data-settings-test-result=""
      data-tone={tone}
      data-testid={testId}
      className={cn("min-w-0", className)}
    >
      <Banner severity={tone} title={message}>
        <div className="min-w-0 space-y-2">
          {hasTiming ? (
            <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted">
              {elapsedMs !== undefined ? (
                <span className="tnum">
                  {t("settings.testResult.elapsed")}: {elapsedMs} ms
                </span>
              ) : null}
              {checkedAt ? (
                <span>
                  {t("settings.testResult.checkedAt")}: {checkedAt}
                </span>
              ) : null}
            </p>
          ) : null}

          {details.length > 0 ? (
            <dl className="grid min-w-0 gap-x-4 gap-y-1 text-xs text-muted sm:grid-cols-2">
              {details.map((detail) => (
                <div key={detail.label} className="min-w-0">
                  <dt className="break-words font-medium text-foreground">{detail.label}</dt>
                  <dd className="break-words tnum">{String(detail.value)}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {tone !== "success" && troubleshooting.length > 0 ? (
            <div className="space-y-1">
              <p className="text-xs font-semibold text-foreground">
                {t("settings.testResult.troubleshooting")}
              </p>
              <ul className="list-disc space-y-1 pl-5 text-xs leading-relaxed text-foreground/90">
                {troubleshooting.map((item) => (
                  <li key={item} className="min-w-0 break-words">
                    <MessageText text={item} />
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {tone === "danger" && errorType ? (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-foreground">
                {t("settings.testResult.errorType")}
              </span>
              <StatusBadge variant="danger" label={errorType} />
            </div>
          ) : null}
        </div>
      </Banner>
    </div>
  );
}

export function toSettingsTestResultDetails(
  values: Readonly<Record<string, SettingsTestResultValue | null | undefined>>
): SettingsTestResultDetail[] {
  return Object.entries(values).flatMap(([label, value]) =>
    value === null || value === undefined || value === "" ? [] : [{ label, value }]
  );
}
