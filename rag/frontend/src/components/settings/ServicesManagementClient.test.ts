import { describe, expect, it } from "vitest";

import {
  serviceExecutionPolicyLabelKey,
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
});
