import { describe, expect, it } from "vitest";

import { parseAnswerDiagnostics } from "@/lib/answer-diagnostics";
import { answerModelCascadeView } from "@/lib/model-cascade";

describe("モデルのカスケードの記録（Issue 4581）", () => {
  it("回答し直した記録は理由を表示名にして読む", () => {
    const data = parseAnswerDiagnostics({
      model_cascade: {
        primary_model: "model-light",
        escalation_model: "model-strong",
        escalated: true,
        reasons: ["structured_output_invalid"],
        stage: "answer.generate",
      },
    });
    expect(data?.modelCascade).toEqual({
      primaryModel: "model-light",
      escalationModel: "model-strong",
      escalated: true,
      reasons: ["structured_output_invalid"],
    });
    expect(answerModelCascadeView(data?.modelCascade)).toEqual({
      escalated: true,
      line: "最初のモデルの出力が検査を通らなかったため、高性能モデルで回答し直しました",
      reasons: "生成の出力が形式の検査を通らなかった",
    });
  });

  it("回答し直さなかった記録は理由を持たず、カスケードしない実行・古い記録は null", () => {
    const kept = parseAnswerDiagnostics({
      model_cascade: { primary_model: "model-light", escalation_model: "model-strong", escalated: false, reasons: ["citation_mismatch"] },
    });
    expect(kept?.modelCascade?.reasons).toEqual([]);
    expect(answerModelCascadeView(kept?.modelCascade)?.escalated).toBe(false);
    expect(parseAnswerDiagnostics({ model_cascade: {} })?.modelCascade).toBeNull();
    expect(parseAnswerDiagnostics({})?.modelCascade).toBeNull();
    expect(answerModelCascadeView(null)).toBeNull();
  });
});
