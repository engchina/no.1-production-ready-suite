import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { ArrowRight, Database, Loader2, RefreshCw, Settings } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useDatabaseStatus } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { t, type I18nKey } from "@/lib/i18n";

/**
 * DB ゲート。設定ページ以外を開く前にデータベースの利用可否を確認する。
 *
 * - 未設定 / 未起動(到達不可)のときは、エラー画面ではなく落ち着いた案内を出し、
 *   データベース設定への導線を示す(取込・検索などの機能ページを保護する)。
 * - 設定ページ(/settings 配下)は DB が無くても到達できるよう、ゲートを通さない。
 * - DB が利用可能なときだけ子(本来のページ)を表示する。
 */
export function DatabaseGate({ children }: { children: ReactNode }) {
  const location = useLocation();
  const isSettingsRoute = location.pathname.startsWith("/settings");
  const query = useDatabaseStatus({ enabled: !isSettingsRoute });

  // 設定ページは常に通す(DB 復旧の導線そのものなので塞がない)。
  if (isSettingsRoute) return <>{children}</>;

  if (query.isPending) {
    return <GateChecking />;
  }

  // ステータス確認自体が失敗(主にバックエンド未起動)。
  if (query.isError) {
    return (
      <GateNotice
        tone="warning"
        titleKey="dbGate.checkFailed.title"
        messageKey="dbGate.checkFailed.message"
        onRetry={() => void query.refetch()}
        isRetrying={query.isFetching}
      />
    );
  }

  switch (query.data?.status) {
    case "ok":
      return <>{children}</>;
    case "not_configured":
      return (
        <GateNotice
          tone="info"
          titleKey="dbGate.notConfigured.title"
          messageKey="dbGate.notConfigured.message"
        />
      );
    case "unreachable":
      return (
        <GateNotice
          tone="warning"
          titleKey="dbGate.unreachable.title"
          messageKey="dbGate.unreachable.message"
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />
      );
    default:
      // data が無い/想定外: 状態を確認できないものとして扱う。
      return (
        <GateNotice
          tone="warning"
          titleKey="dbGate.checkFailed.title"
          messageKey="dbGate.checkFailed.message"
          onRetry={() => void query.refetch()}
          isRetrying={query.isFetching}
        />
      );
  }
}

/** 状態確認中のプレースホルダ。 */
function GateChecking() {
  return (
    <div className="grid min-h-dvh place-items-center p-8">
      <div
        className="flex items-center gap-2 text-sm text-muted"
        role="status"
        aria-live="polite"
      >
        <Loader2 size={16} className="animate-spin" aria-hidden />
        {t("dbGate.checking")}
      </div>
    </div>
  );
}

const TONE_STYLES = {
  info: {
    ring: "bg-info-bg text-info",
  },
  warning: {
    ring: "bg-warning-bg text-warning",
  },
} as const;

/** DB が使えないときの落ち着いた案内(エラーではなく次のアクションを示す)。 */
function GateNotice({
  tone,
  titleKey,
  messageKey,
  onRetry,
  isRetrying = false,
}: {
  tone: keyof typeof TONE_STYLES;
  titleKey: I18nKey;
  messageKey: I18nKey;
  onRetry?: () => void;
  isRetrying?: boolean;
}) {
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <div className="w-full max-w-lg rounded-xl border border-border bg-card p-8 text-center shadow-sm">
        <div
          className={`mx-auto grid size-12 place-items-center rounded-full ${TONE_STYLES[tone].ring}`}
          aria-hidden
        >
          <Database size={22} />
        </div>
        <h1 className="mt-5 text-lg font-semibold text-foreground">{t(titleKey)}</h1>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">{t(messageKey)}</p>

        <div className="mt-6 flex flex-wrap items-center justify-center gap-2.5">
          <Link
            to={APP_ROUTES.settingsDatabase}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium leading-none text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <Settings size={16} aria-hidden />
            {t("dbGate.openDatabaseSettings")}
            <ArrowRight size={16} aria-hidden />
          </Link>
          {onRetry ? (
            <Button variant="secondary" onClick={onRetry} loading={isRetrying}>
              {!isRetrying ? <RefreshCw size={16} aria-hidden /> : null}
              {t("common.retry")}
            </Button>
          ) : null}
        </div>

        <p className="mt-6 border-t border-border pt-4 text-xs leading-relaxed text-muted">
          {t("dbGate.settingsHint")}
        </p>
      </div>
    </div>
  );
}
