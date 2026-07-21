export const DATABASE_READINESS_PATH = "/api/ready/database";
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

type FetchDatabaseReadiness = (
  input: RequestInfo | URL,
  init?: RequestInit
) => Promise<Response>;
type ReportDatabaseUnavailable = (status: DatabaseReadinessSnapshot) => void;

let probeGeneration = 0;
let inFlightProbe: Promise<boolean> | null = null;

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

function reportToApplication(status: DatabaseReadinessSnapshot) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<DatabaseReadinessSnapshot>(DATABASE_UNAVAILABLE_EVENT, {
      detail: status,
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
  report: ReportDatabaseUnavailable = reportToApplication
): Promise<boolean> {
  if (inFlightProbe) return inFlightProbe;

  const generation = ++probeGeneration;
  let probe: Promise<boolean>;
  probe = (async () => {
    try {
      const response = await fetchReadiness(DATABASE_READINESS_PATH, {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "include",
      });
      if (!response.ok) return false;

      const payload = (await response.json()) as { data?: unknown };
      if (!isDatabaseReadinessSnapshot(payload.data) || payload.data.status === "ok") {
        return false;
      }
      if (generation !== probeGeneration) return false;

      report(payload.data);
      return true;
    } catch {
      // readiness 自体を確認できない場合は、元の画面固有エラーを維持する。
      return false;
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
