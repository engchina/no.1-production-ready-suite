/** 例外の本文を持たない、backend と共通の安全な失敗分類（#4412）。 */
export interface FailureDiagnosis {
  kind:
    | "rate_limited"
    | "quota_exhausted"
    | "unavailable"
    | "server_error"
    | "not_supported"
    | "network"
    | "tls"
    | "timeout"
    | "authentication"
    | "permission"
    | "not_found"
    | "bad_request"
    | "too_large"
    | "safety"
    | "invalid_output"
    | "truncated"
    | "unknown";
  network_failure_cause?: "dns" | "refused" | "unreachable" | "reset" | null;
  stage?: string | null;
}

export type FailureDiagnosisReason = FailureDiagnosis["kind"] | "missing" | "dns" | "refused" | "unreachable" | "reset";

const KNOWN_KINDS: ReadonlySet<string> = new Set([
  "rate_limited",
  "quota_exhausted",
  "unavailable",
  "server_error",
  "not_supported",
  "network",
  "tls",
  "timeout",
  "authentication",
  "permission",
  "not_found",
  "bad_request",
  "too_large",
  "safety",
  "invalid_output",
  "truncated",
  "unknown",
]);
const NETWORK_CAUSES: ReadonlySet<string> = new Set(["dns", "refused", "unreachable", "reset"]);

/** 記録された分類だけを投影し、例外の型や任意の追加項目から原因を推定しない。 */
export function failureDiagnosisReason(diagnosis?: FailureDiagnosis | null): FailureDiagnosisReason {
  if (!diagnosis) return "missing";
  if (diagnosis.kind === "network" && diagnosis.network_failure_cause && NETWORK_CAUSES.has(diagnosis.network_failure_cause)) {
    return diagnosis.network_failure_cause;
  }
  return KNOWN_KINDS.has(diagnosis.kind) ? diagnosis.kind : "unknown";
}
