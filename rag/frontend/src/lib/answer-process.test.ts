import { chatProgressStepsFromEvents, traceTimeline, type ChatProgressEvent } from "@production-ready/ui";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  answerProcessStepsFromStages,
  answerStepDisplayName,
  answerStepMeta,
  llmCallsLabel,
  ragTraceDetailsLabels,
  type AnswerStageRecord,
} from "./answer-process";
import { CHAT_PROGRESS_DEFINITIONS } from "./chat-progress";
import { t } from "./i18n";

// 回答の処理の経過（#2096）: チャットと RAG 検索で同じ行（1 工程 = 1 行・字下げ・計測値）を作る。

const T0 = Date.parse("2026-10-16T00:00:00.000Z");

function stage(name: string, outcome: AnswerStageRecord["outcome"], elapsed_ms = 0, at = 0): AnswerStageRecord {
  return { stage: name, outcome, elapsed_ms, receivedAtMs: T0 + at };
}

describe("answerProcessStepsFromStages（RAG 検索）", () => {
  const stages: AnswerStageRecord[] = [
    stage("field_filter", "started"),
    stage("field_filter", "success", 40),
    stage("retrieval", "started"),
    stage("answer_step:質問の理解", "started"),
    stage("answer_step:質問の理解", "success", 120),
    stage("answer_step:回答生成フロー", "started", 0, 200),
    stage("answer_step:文書検索", "started", 0, 210),
    stage("answer_step:Rerank", "started", 0, 900),
  ];

  it("全体を包む工程を除き、入れ子は字下げし、実行中の工程は受け取った時刻から数える", () => {
    const rows = answerProcessStepsFromStages(stages);
    expect(rows.map((row) => [row.label, row.status, row.level ?? 0])).toEqual([
      ["検索条件の読み取り", "done", 0],
      ["質問の条件の読み取り", "done", 0],
      ["根拠の検索と回答の作成", "running", 0],
      ["文書の検索", "running", 1],
      ["検索結果の並べ替え", "running", 2],
    ]);
    expect(rows[1].durationMs).toBe(120);
    expect(rows[4].startedAt).toBe(new Date(T0 + 900).toISOString());
  });

  it("終端で実行中の工程を終わらせ、回答の記録の LLM の回数・結果の説明・失敗を名前の順に足す", () => {
    const finished = answerProcessStepsFromStages(
      [
        ...stages,
        stage("answer_step:Rerank", "success", 300),
        stage("answer_step:文書検索", "error", 800),
        stage("answer_step:文書検索", "started"),
        stage("answer_step:文書検索", "success", 500),
      ],
      {
        terminal: "done",
        executionSteps: [
          { name: "質問の理解", status: "complete", elapsedSeconds: 0.1, llmCalls: 1 },
          { name: "文書検索", status: "エラーで中断", elapsedSeconds: 0.8, llmCalls: 0 },
          { name: "Rerank", status: "該当なし", elapsedSeconds: 0.3, llmCalls: 0 },
          { name: "文書検索", status: "complete", elapsedSeconds: 0.5, llmCalls: 2 },
        ],
      }
    );
    const byLabel = (label: string, nth = 0) => finished.filter((row) => row.label === label)[nth];
    expect(byLabel("質問の条件の読み取り").meta).toBe("LLM 1 回");
    expect(byLabel("根拠の検索と回答の作成").status).toBe("done");
    expect(byLabel("文書の検索").status).toBe("failed");
    expect(byLabel("文書の検索").meta).toBeUndefined();
    expect(byLabel("文書の検索", 1)).toMatchObject({ status: "done", meta: "LLM 2 回", level: 1, durationMs: 500 });
    // 名前の順で対応させるので、Rerank は 1 回目の文書検索の後の記録。
    expect(byLabel("検索結果の並べ替え").meta).toBe("該当なし");
  });

  it("回答の記録の工程の詳細（見出しと値・行）と、パイプラインの工程の詳細を、段を開いて見る詳細にする", () => {
    const details = { fields: [{ label: "結果", value: "条件 2 件" }], lines: ["補足の行"] };
    const rows = answerProcessStepsFromStages(
      [
        stage("answer_step:質問の理解", "started"),
        stage("answer_step:質問の理解", "success", 120),
        stage("answer_guardrail", "started"),
        // 回答の安全チェックの詳細は、成功の段のイベントで届く（#2341）。
        { ...stage("answer_guardrail", "success", 30), details: { fields: [{ label: "結果", value: "問題なし" }] } },
      ],
      {
        terminal: "done",
        executionSteps: [{ name: "質問の理解", status: "complete", elapsedSeconds: 0.1, llmCalls: 1, details }],
      }
    );
    expect(rows[0].details).toEqual(details);
    expect(rows[1].details).toEqual({ fields: [{ label: "結果", value: "問題なし" }] });
  });

  // #2373
  it("終わった工程の詳細と計測値は、工程が終わったときのイベントで後の工程の実行中から出す", () => {
    const rows = answerProcessStepsFromStages([
      stage("answer_step:質問拡張戦略", "started"),
      {
        ...stage("answer_step:質問拡張戦略", "success", 800),
        attributes: { llm_calls: 1, status: "complete" },
        details: { fields: [{ label: "設定", value: "自動" }] },
      },
      stage("answer_step:文書検索", "started"),
    ]);
    expect(rows[0]).toMatchObject({ status: "done", meta: "LLM 1 回", details: { fields: [{ label: "設定", value: "自動" }] } });
    expect(rows[1].status).toBe("running");
  });

  it("無効で省略した安全チェックは成功や失敗へ置き換えず、理由を残す", () => {
    const rows = answerProcessStepsFromStages([
      stage("answer_guardrail", "started"),
      { ...stage("answer_guardrail", "skipped", 1), details: { fields: [{ label: "結果", value: "無効のため省略" }] } },
    ], { terminal: "done" });
    expect(rows[0]).toMatchObject({ status: "skipped", details: { fields: [{ label: "結果", value: "無効のため省略" }] } });
  });

  it("停止した検索の実行中の工程はスキップにする", () => {
    const rows = answerProcessStepsFromStages(
      [stage("answer_step:文書検索", "started"), stage("answer_step:文書検索", "cancelled")],
      { terminal: "cancelled" }
    );
    expect(rows.map((row) => row.status)).toEqual(["skipped"]);
  });
});

describe("answerStepDisplayName", () => {
  it("backend の工程名を利用者向けの名前にし、回数つきの工程は回数を添える。知らない工程名はそのまま", async () => {
    const { answerStepDisplayName } = await import("./answer-process");
    expect(answerStepDisplayName("質問拡張戦略")).toBe("質問の言い換え");
    expect(answerStepDisplayName("Rerank")).toBe("検索結果の並べ替え");
    expect(answerStepDisplayName("文書検索（2回目）")).toBe("文書の検索（2 回目）");
    expect(answerStepDisplayName("回答文の生成と根拠確認（1回目）")).toBe("回答の作成と根拠の確認（1 回目）");
    expect(answerStepDisplayName("検索語の再確定（書き換え後）")).toBe("検索語の決め直し");
    expect(answerStepDisplayName("新しい工程")).toBe("新しい工程");
    expect(answerStepDisplayName("新しい工程（2回目）")).toBe("新しい工程（2回目）");
  });

  it("backend の時間切れの文言の名前（ANSWER_STEP_DISPLAY_NAMES）と同じ名前にする", async () => {
    const { answerStepDisplayName } = await import("./answer-process");
    const source = readFileSync(resolve(__dirname, "../../../backend/app/rag/answer_timeout.py"), "utf-8");
    const block = source.slice(source.indexOf("ANSWER_STEP_DISPLAY_NAMES"), source.indexOf("_STEP_ATTEMPT ="));
    const pairs = [...block.matchAll(/"([^"]+)": "([^"]+)"/g)].map((match) => [match[1], match[2]]);
    expect(pairs.length).toBeGreaterThan(10);
    for (const [name, label] of pairs) expect(answerStepDisplayName(name), name).toBe(label);
  });
});

describe("answerStepMeta", () => {
  it("結果の説明（complete・中断以外）と LLM の回数を「 · 」でつなぐ", () => {
    expect(answerStepMeta({ llmCalls: 0, note: "complete" })).toBeUndefined();
    expect(answerStepMeta({ llmCalls: 3, note: "省略" })).toBe("省略 · LLM 3 回");
    expect(answerStepMeta({ note: "エラーで中断" })).toBeUndefined();
  });
});

describe("チャットの工程の段階（answer_step）", () => {
  it("工程名・入れ子の深さ・LLM の回数と結果の説明から、RAG 検索と同じ行を作る", () => {
    let seq = 0;
    const event = (step_id: string, kind: string, params: Record<string, string | number>): ChatProgressEvent => {
      seq += 1;
      return {
        schema_version: 1,
        seq,
        target_id: "t",
        attempt: 0,
        emitted_at: "2026-10-16T00:00:00.000Z",
        type: "step",
        step_id,
        kind,
        status: "done",
        params,
      };
    };
    const steps = chatProgressStepsFromEvents(
      [
        event("s1", "history_rewrite", {}),
        event("s2", "answer_step", { name: "回答生成フロー", depth: 0 }),
        event("s3", "answer_step", { name: "文書検索", depth: 1, llm_calls: 2, note: "該当なし" }),
      ],
      CHAT_PROGRESS_DEFINITIONS,
      "t"
    );
    expect(steps).toEqual([
      { id: "s1", label: "会話を踏まえた質問の書き換え", status: "done" },
      { id: "s2", label: "根拠の検索と回答の作成", status: "done" },
      { id: "s3", label: "文書の検索", status: "done", meta: "該当なし · LLM 2 回", level: 1 },
    ]);
  });
});

// 送り直しで API への送信が呼び出しより多いとき（#2966）。
describe("LLM の回数と送信の回数", () => {
  it("送り直しで API への送信が多いときだけ、送信の回数も示す", () => {
    expect(answerStepMeta({ llmCalls: 1, llmSends: 2 })).toBe("LLM 1 回・送信 2 回");
    expect(answerStepMeta({ llmCalls: 2, llmSends: 2 })).toBe("LLM 2 回");
    expect(answerStepMeta({ llmCalls: 1 })).toBe("LLM 1 回");
    expect(llmCallsLabel("history.process", 1, 3)).toBe("LLM 1 回・送信 3 回");
  });
});

// #3214: トレースの詳細の span は、処理の経過の行と同じ名前にする（root と同名の段は分ける）。
describe("ragTraceDetailsLabels（トレースの span の名前）", () => {
  const at = (ms: number) => new Date(T0 + ms).toISOString();
  const span = (id: string, parent: string | null, operation_name: string, attributes: Record<string, unknown> = {}) => ({
    span_id: id.repeat(16), parent_span_id: parent?.repeat(16) ?? null, trace_id: "a".repeat(32), operation_name, kind: "internal",
    status: "done", started_at: at(0), finished_at: at(1000), duration_ms: 1000, attributes,
  });
  const summary = { schema_version: 1, completeness: "complete", root_span_id: "1".repeat(16), spans: [
    span("1", null, "rag.answer"),
    span("2", "1", "rag.history_rewrite", { "pr.stage": "history_rewrite" }),
    span("3", "1", "rag.answer", { "pr.stage": "answer" }),
    span("4", "3", "rag.engine.stage", { "pr.stage": "回答生成フロー" }),
    span("5", "4", "rag.engine.stage", { "pr.stage": "文書検索（2回目）" }),
    span("6", "5", "gen_ai.embeddings", { "gen_ai.request.model": "synthetic-embed" }),
    span("7", "1", "rag.answer_guardrail", { "pr.stage": "answer_guardrail" }),
  ] };

  it("処理の経過と同じ名前を付け、root と中の同名の段を分ける", () => {
    const labels = traceTimeline(summary, ragTraceDetailsLabels()).steps.map((step) => step.label);
    expect(labels).toEqual([
      t("trace.span.root"),
      t("search.stage.historyRewrite"),
      t("search.stage.answer"),
      answerStepDisplayName("回答生成フロー"),
      answerStepDisplayName("文書検索（2回目）"),
      "gen_ai.embeddings · synthetic-embed",
      t("search.stage.answerGuardrail"),
    ]);
    expect(labels).not.toContain("rag.engine.stage");
    expect(labels[3]).toBe(t("answer.step.answerFlow"));
  });
});
