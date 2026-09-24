export const DATABASE_READINESS_PATH = "/api/ready/database";
export const PERSISTENCE_STATUS_PATH = "/api/nl2sql/persistence";
export const PERSISTENCE_RECOVERY_PATH = "/api/nl2sql/persistence/recover";
export const DATABASE_UNAVAILABLE_EVENT = "app-database-unavailable";

export type DatabaseReadinessStatus =
  | "ok"
  | "not_configured"
  | "setup_required"
  | "unreachable";

export interface DatabaseReadinessSnapshot {
  status: DatabaseReadinessStatus;
  check: string;
  detail: string | null;
}

export interface PersistenceReadinessSnapshot {
  ready: boolean;
  writable: boolean;
  reason_code: string | null;
  circuit_state?: "closed" | "open" | "half_open";
  retry_after_seconds?: number;
}

export type DatabaseOperationalFailure =
  | { kind: "database"; database: DatabaseReadinessSnapshot }
  | { kind: "persistence"; persistence: PersistenceReadinessSnapshot };

type FetchDatabaseReadiness = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;
type ReportDatabaseUnavailable = (failure: DatabaseOperationalFailure) => void;

let probeGeneration = 0;
let inFlightProbe: Promise<DatabaseOperationalFailure | null> | null = null;

function isDatabaseReadinessSnapshot(value: unknown): value is DatabaseReadinessSnapshot {
  if (typeof value !== "object" || value === null) return false;
  const status = (value as { status?: unknown }).status;
  return (
    status === "ok" ||
    status === "not_configured" ||
    status === "setup_required" ||
    status === "unreachable"
  );
}

function isPersistenceReadinessSnapshot(
  value: unknown
): value is PersistenceReadinessSnapshot {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as { ready?: unknown }).ready === "boolean" &&
    typeof (value as { writable?: unknown }).writable === "boolean"
  );
}

export function reportDatabaseOperationalFailure(failure: DatabaseOperationalFailure) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<DatabaseOperationalFailure>(DATABASE_UNAVAILABLE_EVENT, {
      detail: failure,
    })
  );
}

/** readiness 自体の失敗から再帰的に readiness を呼ばないための判定。 */
export function isDatabaseReadinessRequest(path: string): boolean {
  try {
    return new URL(path, "http://localhost").pathname === DATABASE_READINESS_PATH;
  } catch {
    return path.split(/[?#]/, 1)[0] === DATABASE_READINESS_PATH;
  }
}

/** 4xx は権限・入力エラーとして扱い、5xx のみ DB 状態を再確認する。 */
export function shouldConfirmDatabaseUnavailable(path: string, status: number): boolean {
  return status >= 500 && !isDatabaseReadinessRequest(path);
}

/**
 * 直前のAPI失敗がDB未到達によるものか readiness で確認する。
 * 同時に発生した失敗は1回のprobeへ集約し、明示的な非正常状態だけを通知する。
 */
export function confirmDatabaseUnavailable(
  fetchReadiness: FetchDatabaseReadiness = fetch,
  report: ReportDatabaseUnavailable = reportDatabaseOperationalFailure
): Promise<DatabaseOperationalFailure | null> {
  if (inFlightProbe) return inFlightProbe;

  const generation = ++probeGeneration;
  let probe: Promise<DatabaseOperationalFailure | null>;
  probe = (async () => {
    try {
      const response = await fetchReadiness(DATABASE_READINESS_PATH, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
      });
      if (!response.ok) return null;

      const payload = (await response.json()) as { data?: unknown };
      if (!isDatabaseReadinessSnapshot(payload.data)) {
        return null;
      }
      if (payload.data.status !== "ok") {
        if (generation !== probeGeneration) return null;
        const failure = { kind: "database", database: payload.data } as const;
        report(failure);
        return failure;
      }

      const persistenceResponse = await fetchReadiness(PERSISTENCE_STATUS_PATH, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
      });
      if (!persistenceResponse.ok) return null;
      const persistencePayload = (await persistenceResponse.json()) as { data?: unknown };
      if (
        !isPersistenceReadinessSnapshot(persistencePayload.data) ||
        (persistencePayload.data.ready && persistencePayload.data.writable)
      ) {
        return null;
      }
      if (generation !== probeGeneration) return null;

      const failure = {
        kind: "persistence",
        persistence: persistencePayload.data,
      } as const;
      report(failure);
      return failure;
    } catch {
      // readiness 自体を確認できない場合は、元の画面固有エラーを維持する。
      return null;
    }
  })().finally(() => {
    if (inFlightProbe === probe) inFlightProbe = null;
  });

  inFlightProbe = probe;
  return probe;
}

/** 再試行開始前に古いprobeを無効化し、遅延結果による再ブロックを防ぐ。 */
export function supersedeDatabaseUnavailableProbe() {
  probeGeneration += 1;
  inFlightProbe = null;
}
