import { describe, expect, it } from "vitest";
import { failureDiagnosisReason, type FailureDiagnosis } from "../src/lib/failure-diagnosis";

describe("安全な失敗分類の表示投影", () => {
  it("記録なしと分類不能を区別する", () => {
    expect(failureDiagnosisReason()).toBe("missing");
    expect(failureDiagnosisReason(null)).toBe("missing");
    expect(failureDiagnosisReason({ kind: "unknown" })).toBe("unknown");
  });
  it("不明な将来の分類や原因を本文に転記しない", () => {
    expect(failureDiagnosisReason({ kind: "synthetic-private" } as unknown as FailureDiagnosis)).toBe("unknown");
    expect(failureDiagnosisReason({ kind: "network", network_failure_cause: "synthetic-private" } as unknown as FailureDiagnosis)).toBe("network");
  });
  it.each(["dns", "refused", "unreachable", "reset"] as const)("network の %s だけを採用する", (cause) => {
    expect(failureDiagnosisReason({ kind: "network", network_failure_cause: cause })).toBe(cause);
    expect(failureDiagnosisReason({ kind: "tls", network_failure_cause: cause })).toBe("tls");
  });
});
