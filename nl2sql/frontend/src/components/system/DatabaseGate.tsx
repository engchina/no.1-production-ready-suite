import { useEffect, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { useLocation } from "react-router-dom";

import { Banner } from "@engchina/production-ready-ui";

import { DatabaseUnavailableNotice } from "@/components/system/DatabaseUnavailableNotice";
import {
  DATABASE_UNAVAILABLE_EVENT,
  supersedeDatabaseUnavailableProbe,
} from "@/lib/database-load-error";
import { t, type I18nKey } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import {
  useDatabaseStatus,
  usePersistenceStatus,
  useRecoverPersistence,
} from "@/lib/queries";

const DATABASE_GATE_EXEMPT_ROUTES = [
  APP_ROUTES.settingsOci,
  APP_ROUTES.settingsUploadStorage,
  APP_ROUTES.settingsModel,
  APP_ROUTES.settingsDatabase,
  APP_ROUTES.settingsAppearance,
] as const;

function isDatabaseGateExemptRoute(pathname: string) {
  if (pathname === "/settings") return true;
  return DATABASE_GATE_EXEMPT_ROUTES.some(
    (route) => pathname === route || pathname.startsWith(`${route}/`)
  );
}

/** DB と persisted snapshot の両方が復旧するまで業務ページを描画しない。 */
export function DatabaseGate({ children }: { children: ReactNode }) {
  const location = useLocation();
  const isGateExemptRoute = isDatabaseGateExemptRoute(location.pathname);
  const [reportedUnavailableAt, setReportedUnavailableAt] = useState<number | null>(null);
  const database = useDatabaseStatus({ enabled: !isGateExemptRoute });
  const databaseReady = database.data?.status === "ok";
  const persistence = usePersistenceStatus({
    enabled: !isGateExemptRoute && databaseReady,
  });
  const recover = useRecoverPersistence();

  useEffect(() => {
    const handleDatabaseUnavailable = () => setReportedUnavailableAt(Date.now());
    window.addEventListener(DATABASE_UNAVAILABLE_EVENT, handleDatabaseUnavailable);
    return () =>
      window.removeEventListener(DATABASE_UNAVAILABLE_EVENT, handleDatabaseUnavailable);
  }, []);

  useEffect(() => {
    if (
      reportedUnavailableAt === null ||
      database.data?.status !== "ok" ||
      database.dataUpdatedAt <= reportedUnavailableAt ||
      !persistence.data?.ready ||
      persistence.dataUpdatedAt <= reportedUnavailableAt
    ) {
      return;
    }
    setReportedUnavailableAt(null);
  }, [
    database.data?.status,
    database.dataUpdatedAt,
    persistence.data?.ready,
    persistence.dataUpdatedAt,
    reportedUnavailableAt,
  ]);

  useEffect(() => {
    if (
      isGateExemptRoute ||
      !databaseReady ||
      !persistence.data ||
      persistence.data.ready ||
      recover.isPending ||
      recover.isError
    ) {
      return;
    }
    recover.mutate();
  }, [databaseReady, isGateExemptRoute, persistence.data, recover]);

  if (isGateExemptRoute) return <>{children}</>;

  const returnTo = `${location.pathname}${location.search}${location.hash}`;
  const retry = async () => {
    supersedeDatabaseUnavailableProbe();
    recover.reset();
    const databaseResult = await database.refetch();
    if (databaseResult.data?.status !== "ok") return;

    const persistenceResult = await persistence.refetch();
    if (persistenceResult.data?.ready) setReportedUnavailableAt(null);
  };

  if (reportedUnavailableAt !== null) {
    return (
      <DatabaseUnavailableNotice
        returnTo={returnTo}
        onRetry={() => void retry()}
        isRetrying={database.isFetching || persistence.isFetching || recover.isPending}
      />
    );
  }

  if (database.isPending) return <GateChecking />;
  if (database.isError || database.data?.status !== "ok") {
    return (
      <DatabaseUnavailableNotice
        returnTo={returnTo}
        onRetry={() => void retry()}
        isRetrying={database.isFetching}
      />
    );
  }

  if (persistence.isPending || recover.isPending) {
    return <GateChecking labelKey="dbGate.recovering" />;
  }
  if (persistence.isError || recover.isError || !persistence.data?.ready) {
    return (
      <DatabaseUnavailableNotice
        returnTo={returnTo}
        onRetry={() => void retry()}
        isRetrying={persistence.isFetching || recover.isPending}
      />
    );
  }

  return (
    <>
      {persistence.data.mode === "memory" ? (
        <div className="px-4 pt-4 lg:px-8 lg:pt-6">
          <Banner severity="warning" title={t("persistence.memoryWarning.title")}>
            {t("persistence.memoryWarning.message")}
          </Banner>
        </div>
      ) : null}
      {children}
    </>
  );
}

function GateChecking({ labelKey = "dbGate.checking" }: { labelKey?: I18nKey }) {
  return (
    <div className="grid min-h-dvh place-items-center p-6">
      <div className="flex items-center gap-2 text-sm text-muted" role="status" aria-live="polite">
        <Loader2 size={16} className="animate-spin" aria-hidden />
        {t(labelKey)}
      </div>
    </div>
  );
}
