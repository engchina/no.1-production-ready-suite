import { ArrowRight, Database, RefreshCw, Settings } from "lucide-react";
import { Link } from "react-router-dom";

import { Banner } from "@engchina/production-ready-ui";

import { Button, buttonVariants } from "@/components/ui/button";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

const DATABASE_SETTINGS_TARGET = `${APP_ROUTES.settingsDatabase}#adb-management`;

export function DatabaseUnavailableNotice({
  mode = "gate",
  returnTo,
  onRetry,
  isRetrying = false,
}: {
  mode?: "gate" | "banner";
  returnTo?: string;
  onRetry: () => void;
  isRetrying?: boolean;
}) {
  if (mode === "banner") {
    return (
      <Banner
        severity="warning"
        title={t("dbGate.unreachable.title")}
        action={
          <Button size="md" variant="secondary" onClick={onRetry} loading={isRetrying}>
            <RefreshCw size={15} aria-hidden />
            {t("common.retry")}
          </Button>
        }
      />
    );
  }

  return (
    <div className="grid min-h-dvh place-items-center p-4 sm:p-6">
      <section
        className="w-full max-w-lg rounded-xl border border-border bg-card p-6 text-center shadow-sm sm:p-8"
        aria-labelledby="database-unavailable-title"
      >
        <div
          className="mx-auto grid size-12 place-items-center rounded-full bg-warning-bg text-warning"
          aria-hidden
        >
          <Database size={22} />
        </div>
        <h1
          id="database-unavailable-title"
          className="mt-5 text-lg font-semibold text-foreground"
        >
          {t("dbGate.unreachable.title")}
        </h1>
        <NoticeContent
          returnTo={returnTo}
          onRetry={onRetry}
          isRetrying={isRetrying}
        />
      </section>
    </div>
  );
}

function NoticeContent({
  returnTo,
  onRetry,
  isRetrying,
}: {
  returnTo?: string;
  onRetry: () => void;
  isRetrying: boolean;
}) {
  return (
    <>
      <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted">
        {t("dbGate.unreachable.message")}
      </p>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-2">
        <Link
          to={DATABASE_SETTINGS_TARGET}
          state={returnTo ? { returnTo } : undefined}
          className={buttonVariants({ variant: "primary", size: "md" })}
        >
          <Settings size={15} aria-hidden />
          {t("dbGate.openDatabaseSettings")}
          <ArrowRight size={15} aria-hidden />
        </Link>
        <Button size="md" variant="secondary" onClick={onRetry} loading={isRetrying}>
          <RefreshCw size={15} aria-hidden />
          {t("common.retry")}
        </Button>
      </div>

      <p className="mt-6 border-t border-border pt-4 text-xs leading-relaxed text-muted">
        {t("dbGate.settingsHint")}
      </p>
    </>
  );
}
