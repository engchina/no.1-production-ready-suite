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
        reasons: ["citation_mismatch", "conclusion_withheld"],
        stage: "answer.generate",
      },
    });
    expect(data?.modelCascade).toEqual({
      primaryModel: "model-light",
      escalationModel: "model-strong",
      escalated: true,
      reasons: ["citation_mismatch", "conclusion_withheld"],
    });
    expect(answerModelCascadeView(data?.modelCascade)).toEqual({
      escalated: true,
      line: "最初のモデルの回答が確認を通らなかったため、高性能モデルで回答し直しました",
      reasons: "引用の ID・原文が資料と一致しなかった、要約・結論を確かめられず保留した",
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
