import { Link } from "react-router-dom";
import { ArrowRight, RefreshCw, Settings } from "lucide-react";

import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";

/**
 * DB 停止/応答不良で閲覧系 API が縮退応答(空データ + warning)したときに、
 * ページ上部へ常設する非ブロッキングなお知らせ。
 *
 * 全画面エラーにはせず、ページ本体(空状態)はそのまま表示しつつ、
 * 落ち着いた warning トーンで状況と復旧導線(再試行 / DB 設定)を示す。
 * docs/frontend-messaging-spec.md の Banner(状況提示)チャネルに従う。
 */
export function DegradedBanner({
  messages,
  onRetry,
  isRetrying = false,
  className,
}: {
  /** バックエンドが返した warning_messages。空なら何も表示しない。 */
  messages: readonly string[] | undefined;
  onRetry?: () => void;
  isRetrying?: boolean;
  className?: string;
}) {
  if (!messages || messages.length === 0) return null;

  return (
    <Banner
      severity="warning"
      title={t("common.degraded.title")}
      className={className}
      action={
        <>
          {onRetry ? (
            <Button variant="secondary" size="sm" onClick={onRetry} loading={isRetrying}>
              {!isRetrying ? <RefreshCw size={14} aria-hidden /> : null}
              {t("common.retry")}
            </Button>
          ) : null}
          <Link
            to={APP_ROUTES.settingsDatabase}
            className="inline-flex h-8 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <Settings size={14} aria-hidden />
            {t("common.degraded.openDatabaseSettings")}
            <ArrowRight size={14} aria-hidden />
          </Link>
        </>
      }
    >
      {messages.length === 1 ? (
        <p>{messages[0]}</p>
      ) : (
        <ul className="list-disc space-y-0.5 pl-4">
          {messages.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      )}
    </Banner>
  );
}
