import type { ReactNode } from "react";
import { Banner, Disclosure } from "@engchina/production-ready-ui";

import { presentApiError } from "@/lib/api-error-presentation";
import { t } from "@/lib/i18n";

/**
 * API の失敗を danger の Banner で出す（UX 契約 messaging.md §10.3。#900）。
 *
 * 1 文目に何が起きたか、次に次の操作を出し、技術的な詳細（要求・上限・エラー種別・元の文・
 * request ID）は「詳細」に畳んで失敗のときは開いて出す（`SettingsTestResultPanel` と同じ形）。
 * `summary` / `nextAction` を渡すと、画面の操作に合わせた文で要約を差し替える。
 */
export function ApiErrorBanner({
  error,
  fallback,
  summary,
  nextAction,
  action,
  testId,
}: {
  error: unknown;
  fallback: string;
  summary?: string;
  nextAction?: string;
  action?: ReactNode;
  testId?: string;
}) {
  const presentation = presentApiError(error, fallback);
  const next = nextAction ?? presentation.nextAction;
  return (
    <div className="min-w-0" data-testid={testId}>
      <Banner
        severity="danger"
        title={summary ?? presentation.summary}
        action={action}
      >
        {next || presentation.details.length > 0 ? (
          <div className="min-w-0 space-y-2">
            {next ? <p>{next}</p> : null}
            {presentation.details.length > 0 ? (
              <Disclosure
                variant="plain"
                size="sm"
                summary={t("api.error.details")}
                defaultOpen
              >
                <dl className="grid min-w-0 gap-x-4 gap-y-1 text-xs text-fg-muted sm:grid-cols-2">
                  {presentation.details.map((item) => (
                    <div key={item.label} className="min-w-0">
                      <dt className="break-words font-medium text-fg">
                        {item.label}
                      </dt>
                      <dd className="break-all">{item.value}</dd>
                    </div>
                  ))}
                </dl>
              </Disclosure>
            ) : null}
          </div>
        ) : null}
      </Banner>
    </div>
  );
}
