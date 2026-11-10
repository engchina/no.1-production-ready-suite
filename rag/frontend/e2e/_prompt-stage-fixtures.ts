export type StageFixture = { id: string; phase: string; prompts: { id: string; content: string; prompt_key?: string }[] };

export const DEFAULT_STAGES: StageFixture[] = [
  { id: "generation", phase: "generation", prompts: [{ id: "batchReading", content: "## Step 1: 選択した全文を分けて読む\n原文引用付きの回答候補を返す。" }, { id: "role", content: "## Step 1: 合成の役割の文", prompt_key: "generation.role" }, { id: "fixed", content: "## Step 2: 合成の固定の指示" }, { id: "assumptionSystem", content: "## 回答の前提\n- 未確定の条件を明示する。" }, { id: "template", content: "## Step 1: 合成の入力テンプレート" }] },
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
  { id: "screen_linking", phase: "retrieval", prompts: [{ id: "system", content: "画面の system" }] },
  { id: "faq_answer", phase: "generation", prompts: [{ id: "system", content: "FAQ の指示" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "batchReading", content: "## Step 1: 選択した全文を分けて読む\n原文引用付きの回答候補を返す。" }, { id: "role", content: "## Step 1: 合成の役割の文", prompt_key: "generation.role" }, { id: "fixed", content: "## Step 2: 合成の固定の指示" }, { id: "assumptionSystem", content: "## 回答の前提\n- 未確定の条件を明示する。" }, { id: "template", content: "## Step 1: 合成の入力テンプレート" }] },
  { id: "evaluation", phase: "evaluation", prompts: [{ id: "correctness", content: "正確性の指示" }] },
];

