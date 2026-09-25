import { describe, expect, it } from "vitest";

import { t } from "@/lib/i18n";

import {
  serviceExecutionPolicyLabelKey,
  servicePrimaryAction,
  serviceStoppedHintKey,
} from "./ServicesManagementClient";

describe("ServicesManagementClient service policy helpers", () => {
  it("maps execution policies to compact badge labels", () => {
    expect(serviceExecutionPolicyLabelKey("required_no_fallback")).toBe(
      "settings.services.executionPolicy.requiredNoFallback"
    );
    expect(serviceExecutionPolicyLabelKey("in_process_when_disabled")).toBe(
      "settings.services.executionPolicy.inProcessWhenDisabled"
    );
    expect(serviceExecutionPolicyLabelKey("selected_adapter")).toBe(
      "settings.services.executionPolicy.selectedAdapter"
    );
  });

  it("maps stopped services to the right clarification text", () => {
    expect(serviceStoppedHintKey("required_no_fallback")).toBe(
      "settings.services.requiredStoppedHint"
    );
    expect(serviceStoppedHintKey("in_process_when_disabled")).toBe(
      "settings.services.optionalStoppedHint.inProcess"
    );
    expect(serviceStoppedHintKey("selected_adapter")).toBe(
      "settings.services.optionalStoppedHint.selectedAdapter"
    );
  });

  // deployable=false 段は「backend 内処理」固定表示 + 補足文を出す(操作系は非表示)。
  it("provides an in_process status label and a future-service hint", () => {
    expect(t("settings.services.status.in_process")).toBe("backend 内処理");
    expect(t("settings.services.futureServiceHint")).toContain("将来");
  });

  // 行に常に出す主操作は状態に応じて 1 つだけ（#158）。
  it("picks one state-based primary lifecycle action per row", () => {
    expect(servicePrimaryAction("running")).toBe("stop");
    expect(servicePrimaryAction("degraded")).toBe("stop");
    expect(servicePrimaryAction("stopped")).toBe("start");
    expect(servicePrimaryAction("unconfigured")).toBe("start");
    expect(servicePrimaryAction("loading")).toBe("start");
    expect(servicePrimaryAction("error")).toBe("start");
  });
});
