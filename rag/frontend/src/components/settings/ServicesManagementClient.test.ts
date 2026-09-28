import { describe, expect, it } from "vitest";

import { t } from "@/lib/i18n";

import {
  serviceCanRestart,
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

  // systemd の unit の状態（#286）。起動中は unit が動いているので「停止」、失敗・未登録は「起動」。
  it("maps systemd unit states to the primary action and restart availability", () => {
    expect(servicePrimaryAction("starting")).toBe("stop");
    expect(servicePrimaryAction("failed")).toBe("start");
    expect(servicePrimaryAction("not_installed")).toBe("start");
    expect(serviceCanRestart("running")).toBe(true);
    expect(serviceCanRestart("degraded")).toBe(true);
    expect(serviceCanRestart("starting")).toBe(true);
    expect(serviceCanRestart("stopped")).toBe(false);
    expect(serviceCanRestart("failed")).toBe(false);
    expect(serviceCanRestart("not_installed")).toBe(false);
  });

  it("labels systemd states and journald logs without docker wording", () => {
    expect(t("settings.services.status.starting")).toBe("起動中");
    expect(t("settings.services.status.failed")).toBe("起動失敗");
    expect(t("settings.services.status.not_installed")).toBe("未登録");
    expect(
      t("settings.services.logs.source.journald", {
        unit: "production-ready-rag-parser-docling.service",
        lines: "200",
      })
    ).toBe("journalctl -u production-ready-rag-parser-docling.service / 最新 200 行");
    for (const key of [
      "settings.services.overview.description",
      "settings.services.mode.dev",
      "settings.services.mode.prod",
      "settings.services.mode.dev.hint",
      "settings.services.mode.prod.hint",
      "settings.services.gpuNote",
      "settings.services.modelCache.hint",
    ] as const) {
      expect(t(key)).not.toMatch(/docker|compose|コンテナ/i);
    }
    // 既定の解析エンジンは Docling（#286）。
    expect(t("settings.services.cpuNote")).toContain("Docling は既定の解析エンジン");
  });
});
