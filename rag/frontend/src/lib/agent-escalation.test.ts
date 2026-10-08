import { describe, expect, it } from "vitest";

import { AGENT_ESCALATION_QUESTION_MAX_CHARS, agentEscalationUrl } from "./agent-escalation";
import { parseAnswerDiagnostics, parseAnswerRoute } from "./answer-diagnostics";

const suggested = {
  path: "rag",
  reason: "needs_environment_data",
  escalationSuggested: true,
  escalationReason: "needs_environment_data",
};

describe("parseAnswerRoute", () => {
  it("経路の記録を読む", () => {
    expect(
      parseAnswerDiagnostics({
        route: {
          path: "rag",
          reason: "needs_environment_data",
          escalation_suggested: true,
          escalation_reason: "needs_environment_data",
        },
      })?.route,
    ).toEqual(suggested);
  });

  it("経路の無い回答は null", () => {
    expect(parseAnswerRoute(undefined)).toBeNull();
    expect(parseAnswerDiagnostics({})?.route).toBeNull();
  });
});

describe("agentEscalationUrl", () => {
  it("質問・入口・理由を query に入れた Agent のチャットの URL を作る", () => {
    const url = new URL(
      agentEscalationUrl("https://agent.example.com/chat", suggested, " 今の設定値は？ ") ?? "",
    );
    expect(url.origin + url.pathname).toBe("https://agent.example.com/chat");
    expect(url.searchParams.get("question")).toBe("今の設定値は？");
    expect(url.searchParams.get("entry")).toBe("rag_escalation");
    expect(url.searchParams.get("reason")).toBe("needs_environment_data");
  });

  it("提案が無い・URL が未設定・質問が空なら出さない", () => {
    expect(
      agentEscalationUrl("https://agent.example.com/chat", { ...suggested, escalationSuggested: false }, "q"),
    ).toBeNull();
    expect(agentEscalationUrl(null, suggested, "q")).toBeNull();
    expect(agentEscalationUrl("https://agent.example.com/chat", null, "q")).toBeNull();
    expect(agentEscalationUrl("https://agent.example.com/chat", suggested, "  ")).toBeNull();
    expect(agentEscalationUrl("javascript:alert(1)", suggested, "q")).toBeNull();
  });

  it("長い質問は上限で切る", () => {
    const url = new URL(
      agentEscalationUrl("https://agent.example.com/chat", suggested, "あ".repeat(5000)) ?? "",
    );
    expect(url.searchParams.get("question")).toHaveLength(AGENT_ESCALATION_QUESTION_MAX_CHARS);
  });
});
