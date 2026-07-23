import { useEffect, useState, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";

import { Banner } from "@engchina/production-ready-ui";

import {
  DatabaseUnavailableNotice,
  type DatabaseNoticeStatus,
} from "@/components/system/DatabaseUnavailableNotice";
import {
  DATABASE_UNAVAILABLE_EVENT,
  supersedeDatabaseUnavailableProbe,
  type DatabaseOperationalFailure,
} from "@/lib/database-load-error";
import { t, type I18nKey } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import {
  useDatabaseStatus,
  usePersistenceStatus,
  useRecoverPersistence,
  queryKeys,
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
  const queryClient = useQueryClient();
  const isGateExemptRoute = isDatabaseGateExemptRoute(location.pathname);
  const [reportedFailure, setReportedFailure] = useState<{
    at: number;
    failure: DatabaseOperationalFailure;
  } | null>(null);
  const database = useDatabaseStatus({ enabled: !isGateExemptRoute });
  const databaseReady = database.data?.status === "ok";
  const persistence = usePersistenceStatus({
    enabled: !isGateExemptRoute && databaseReady,
  });
  const recover = useRecoverPersistence();

  useEffect(() => {
    const handleDatabaseUnavailable = (event: Event) => {
      const failure = (event as CustomEvent<DatabaseOperationalFailure>).detail;
      const at = Date.now();
      setReportedFailure({ at, failure });
      if (failure.kind === "database") {
        queryClient.setQueryData(queryKeys.databaseStatus, failure.database);
      } else {
        queryClient.setQueryData(queryKeys.persistenceStatus, (current: unknown) => ({
          ...(typeof current === "object" && current !== null ? current : {}),
          ...failure.persistence,
        }));
      }
    };
    window.addEventListener(DATABASE_UNAVAILABLE_EVENT, handleDatabaseUnavailable);
    return () =>
      window.removeEventListener(DATABASE_UNAVAILABLE_EVENT, handleDatabaseUnavailable);
  }, [queryClient]);

  useEffect(() => {
    if (
      reportedFailure === null ||
      database.data?.status !== "ok" ||
      database.dataUpdatedAt <= reportedFailure.at ||
      !persistence.data?.ready ||
      persistence.dataUpdatedAt <= reportedFailure.at
    ) {
      return;
    }
    setReportedFailure(null);
  }, [
    database.data?.status,
    database.dataUpdatedAt,
    persistence.data?.ready,
    persistence.dataUpdatedAt,
    reportedFailure,
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

    let persistenceResult = await persistence.refetch();
    if (!persistenceResult.data?.ready) {
      try {
        await recover.mutateAsync();
      } catch {
        return;
      }
      persistenceResult = await persistence.refetch();
    }
    if (persistenceResult.data?.ready) setReportedFailure(null);
  };

  if (reportedFailure !== null) {
    const reportedStatus: DatabaseNoticeStatus =
      reportedFailure.failure.kind === "persistence"
        ? "persistence"
        : noticeStatusForDatabase(reportedFailure.failure.database.status);
    const reasonCode =
      reportedFailure.failure.kind === "persistence"
        ? reportedFailure.failure.persistence.reason_code
        : reportedFailure.failure.database.check;
    return (
      <DatabaseUnavailableNotice
        returnTo={returnTo}
        onRetry={() => void retry()}
        isRetrying={database.isFetching || persistence.isFetching || recover.isPending}
        status={reportedStatus}
        reasonCode={reasonCode}
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
        status={
          database.isError
            ? "check_failed"
            : noticeStatusForDatabase(database.data?.status)
        }
        reasonCode={database.data?.check}
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
        status="persistence"
        reasonCode={persistence.data?.reason_code}
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

function noticeStatusForDatabase(
  status: "ok" | "not_configured" | "setup_required" | "unreachable" | undefined
): DatabaseNoticeStatus {
  return status === "not_configured" || status === "setup_required" || status === "unreachable"
    ? status
    : "check_failed";
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
