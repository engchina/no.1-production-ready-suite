export type StageFixture = { id: string; phase: string; prompts: { id: string; content: string }[] };

export const DEFAULT_STAGES: StageFixture[] = [
  { id: "field_filter", phase: "question", prompts: [{ id: "system", content: "条件の指示" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "batchReading", content: "## Step 1: 選択した全文を分けて読む\n原文引用付きの回答候補を返す。" }, { id: "system", content: "生成の system" }, { id: "plannedSystem", content: "## Step 1: 入力の根拠計画に沿って回答する\n計画を作り直さず原文から回答する。" }, { id: "assumptionSystem", content: "## 回答の前提\n- 未確定の条件を明示する。" }] },
];

// 8 つの区分にまたがる段（backend の PROMPT_STAGES の一部。内容は合成）。
export const FLOW_STAGES: StageFixture[] = [
  {
    id: "picture_description",
    phase: "ingestion",
    prompts: [
      { id: "system", content: "図の system" },
      { id: "template", content: "## 現在の図の読み取り指示" },
      { id: "table", content: "表の指示" },
    ],
  },
  { id: "heading_levels", phase: "ingestion", prompts: [{ id: "instruction", content: "階層の指示" }] },
  { id: "field_extraction", phase: "ingestion", prompts: [{ id: "instruction", content: "項目の指示" }] },
  { id: "support_guide_draft", phase: "knowledge", prompts: [{ id: "system", content: "下書きの指示" }] },
  { id: "support_guide_conditions", phase: "question", prompts: [{ id: "system", content: "## Step 1: 質問と条件を読む\n合成の条件。\n\n## Step 2: 原文から値を抜き出す\n合成の原文。\n\n## Step 3: 条件の状態を判定する\nknown / unknown / conflicting。\n\n## Step 4: 結果を返す\nJSON のみ。" }] },
  { id: "history_rewrite", phase: "question", prompts: [{ id: "system", content: "書き換えの system" }] },
  { id: "field_filter", phase: "question", prompts: [{ id: "system", content: "条件の指示" }] },
  { id: "screen_linking", phase: "retrieval", prompts: [{ id: "system", content: "画面の system" }] },
  { id: "article_catalog", phase: "retrieval", prompts: [{ id: "system", content: "目録の指示" }] },
  { id: "section_extraction", phase: "grounding", prompts: [{ id: "system", content: "抽出の指示" }] },
  { id: "faq_answer", phase: "generation", prompts: [{ id: "system", content: "FAQ の指示" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "batchReading", content: "## Step 1: 選択した全文を分けて読む\n原文引用付きの回答候補を返す。" }, { id: "system", content: "生成の system" }, { id: "plannedSystem", content: "## Step 1: 入力の根拠計画に沿って回答する\n計画を作り直さず原文から回答する。" }, { id: "assumptionSystem", content: "## 回答の前提\n- 未確定の条件を明示する。" }] },
  { id: "evaluation", phase: "evaluation", prompts: [{ id: "scope", content: "範囲の指示" }, { id: "system", content: "網羅性・忠実性の指示" }, { id: "correctness", content: "正確性の指示" }] },
];

