import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ANSWER_STAGE_LABEL,
  ANSWER_STEP_STAGE_PREFIX,
  answerProgressLabel,
  answerStageLabel,
  currentAnswerStage,
  type AnswerStageEvent,
} from "./answer-progress";
import { t } from "./i18n";

describe("answer-progress（Issue 375）", () => {
  it("今の工程は、終わっていない最後の工程（入れ子の内側が終われば外側）", () => {
    const events: AnswerStageEvent[] = [
      { stage: "agentic_planning", outcome: "started" },
      { stage: "agentic_planning", outcome: "success" },
      { stage: "embedding", outcome: "started" },
    ];
    expect(currentAnswerStage([])).toBeNull();
    expect(currentAnswerStage(events.slice(0, 1))).toBe("agentic_planning");
    expect(currentAnswerStage(events)).toBe("embedding");
    const nested: AnswerStageEvent[] = [
      { stage: "agentic_multi_hop_retrieval", outcome: "started" },
      { stage: "context_expansion", outcome: "started" },
      { stage: "context_expansion", outcome: "success" },
    ];
    expect(currentAnswerStage(nested)).toBe("agentic_multi_hop_retrieval");
    // どれも実行中でなければ最後に通知された工程。
    expect(
      currentAnswerStage([...nested, { stage: "agentic_multi_hop_retrieval", outcome: "success" }])
    ).toBe("agentic_multi_hop_retrieval");
  });

  it("処理中の文言に今の工程を入れる。未開始は検索の準備", () => {
    expect(answerProgressLabel([])).toBe("回答を生成しています（検索の準備）");
    expect(answerProgressLabel([{ stage: "agentic_planning", outcome: "started" }])).toBe(
      "回答を生成しています（検索の計画）"
    );
    expect(answerProgressLabel([{ stage: "agentic_multi_hop", outcome: "started" }])).toBe(
      "回答を生成しています（追加の検索の計画）"
    );
    expect(answerStageLabel("unknown_stage")).toBe(t("search.stage.processing"));
  });

  it("工程の名前は backend の時間切れの文言（ANSWER_STAGE_LABELS）と同じ", () => {
    const source = readFileSync(
      resolve(__dirname, "../../../backend/app/rag/answer_timeout.py"),
      "utf-8"
    );
    const block = source.slice(
      source.indexOf("ANSWER_STAGE_LABELS: dict[str, str] = {"),
      source.indexOf("}", source.indexOf("ANSWER_STAGE_LABELS: dict[str, str] = {"))
    );
    const backendLabels = Object.fromEntries(
      [...block.matchAll(/"([a-z_]+)": "([^"]+)"/g)].map((match) => [match[1], match[2]])
    );
    expect(Object.keys(backendLabels).length).toBeGreaterThan(10);
    for (const [stage, label] of Object.entries(backendLabels)) {
      expect(ANSWER_STAGE_LABEL[stage], stage).toBeDefined();
      expect(answerStageLabel(stage), stage).toBe(label);
    }
    const preparing = /ANSWER_STAGE_BEFORE_START_LABEL = "([^"]+)"/.exec(source)?.[1];
    expect(t("answer.progress.preparing")).toBe(preparing);
  });

  it("回答フローの各工程（Issue 593）は工程名をそのまま出し、入れ子の内側を今の工程にする", () => {
    const docragSource = readFileSync(
      resolve(__dirname, "../../../backend/app/rag/docrag_answer.py"),
      "utf-8"
    );
    expect(docragSource).toContain(`ANSWER_STEP_STAGE_PREFIX = "${ANSWER_STEP_STAGE_PREFIX}"`);
    expect(answerStageLabel("answer_step:文書検索（1回目）")).toBe("文書検索（1回目）");
    expect(answerStageLabel("answer_step:")).toBe(t("search.stage.processing"));
    const events: AnswerStageEvent[] = [
      { stage: "docrag_answer", outcome: "started" },
      { stage: "answer_step:質問の理解", outcome: "started" },
      { stage: "answer_step:質問の理解", outcome: "success" },
      { stage: "answer_step:文書検索", outcome: "started" },
    ];
    expect(answerProgressLabel(events)).toBe("回答を生成しています（文書検索）");
    expect(answerProgressLabel(events.slice(0, 3))).toBe(
      "回答を生成しています（根拠の検索と回答の生成）"
    );
  });
});
