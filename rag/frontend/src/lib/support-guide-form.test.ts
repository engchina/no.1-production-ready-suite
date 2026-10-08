import { describe, expect, it } from "vitest";

import type { SupportGuideChange, SupportGuideContent } from "./api";
import {
  aliasesToText,
  changeFieldLabels,
  changeSectionLabel,
  countChanges,
  groupChangesBySection,
  tryConditionOptions,
  tryConditionsPayload,
  textToAliases,
  emptyGuideForm,
  guideContentFromForm,
  guideFormFromContent,
  guideFormIssues,
  guideFormSnapshot,
  issuePathLabel,
  moveItem,
  newBranchRow,
  newConditionRow,
  newStepRow,
  nextRowId,
  normalizeIssuePath,
  parseSupportGuideImport,
  removeStep,
  renameConditionId,
  renameStepId,
  splitLines,
  supportGuideExportFileName,
  supportGuideState,
} from "./support-guide-form";

const content: SupportGuideContent = {
  schema_version: 1,
  title: "パスワードの再設定",
  description: "社内システムのパスワードを戻す",
  goal: {
    expected_result: "利用者が再設定の手順を終えられる",
    intent_examples: ["パスワードを忘れた"],
    match_terms: ["パスワード", "再設定"],
  },
  applicability: {
    business_domains: ["情報システム"],
    object_types: [],
    versions: [],
    effective_from: "2026-10-01",
    effective_to: null,
  },
  conditions: [
    {
      id: "account",
      label: "アカウントの種類",
      type: "enum",
      allowed_values: ["社員", "派遣"],
      required: true,
      source: "user",
      unknown_handling: "ask",
      question: "アカウントの種類は？",
      value_aliases: { 社員: ["正社員"] },
    },
  ],
  steps: [
    {
      id: "check",
      title: "本人を確かめる",
      purpose: "",
      depends_on: [],
      retrieval_hints: ["本人確認"],
      evidence_requirements: [],
      allowed_tools: ["rag_search"],
      done_when: "",
    },
    {
      id: "reset",
      title: "再設定する",
      purpose: "",
      depends_on: ["check"],
      retrieval_hints: [],
      evidence_requirements: ["手順書の節"],
      allowed_tools: ["rag_read_source", "rag_search"],
      done_when: "新しいパスワードで入れる",
    },
  ],
  branches: [
    {
      id: "staff",
      when: { condition_id: "account", operator: "equals", values: ["社員"] },
      goto_step: "reset",
      note: "",
    },
  ],
  references: [{ document_id: "doc-1", title: "運用手順書", section_path: ["3 章"], version: "" }],
  completion: [{ id: "done", description: "入れたことを確かめた", check_method: "" }],
  impact: { scope: "individual", approval_required: false, approval_note: "" },
  handoff: { conditions: ["本人を確かめられない"], contact: "ヘルプデスク" },
};

describe("splitLines", () => {
  it("前後の空白・空行・重複を除く", () => {
    expect(splitLines(" a \n\nb\na\n  \n")).toEqual(["a", "b"]);
  });
});

describe("フォームと内容の変換", () => {
  it("内容 → フォーム → 内容で同じになる（道具は許可の一覧の順にそろえる）", () => {
    const roundTrip = guideContentFromForm(guideFormFromContent(content));
    expect(roundTrip).toEqual({
      ...content,
      steps: [
        content.steps[0],
        { ...content.steps[1], allowed_tools: ["rag_search", "rag_read_source"] },
      ],
    });
  });

  it("選択肢は型が選択のときだけ送り、「分からない」の分岐は値を送らない", () => {
    const form = guideFormFromContent(content);
    form.conditions[0] = { ...form.conditions[0], type: "text" };
    form.branches[0] = { ...form.branches[0], operator: "unknown" };
    const result = guideContentFromForm(form);
    expect(result.conditions[0].allowed_values).toEqual([]);
    expect(result.branches[0].when.values).toEqual([]);
  });

  it("行の key と空行の違いは未保存の変更にしない", () => {
    const a = guideFormFromContent(content);
    const b = guideFormFromContent(content);
    expect(a.steps[0].key).not.toBe(b.steps[0].key);
    expect(guideFormSnapshot(a)).toBe(guideFormSnapshot(b));
    b.matchTerms = `${b.matchTerms}\n`;
    expect(guideFormSnapshot(b)).toBe(guideFormSnapshot(a));
    b.title = "変えた";
    expect(guideFormSnapshot(b)).not.toBe(guideFormSnapshot(a));
  });
});

describe("行の操作", () => {
  it("新しい行の ID は既存と重ならない", () => {
    expect(nextRowId("step", ["step1", "step3"])).toBe("step2");
    const form = guideFormFromContent(content);
    expect(newStepRow(form).id).toBe("step1");
    expect(newConditionRow(form).id).toBe("cond1");
    expect(newBranchRow(form).id).toBe("branch1");
  });

  it("moveItem は端を越えない", () => {
    expect(moveItem(["a", "b", "c"], 0, 1)).toEqual(["b", "a", "c"]);
    expect(moveItem(["a", "b", "c"], 0, -1)).toEqual(["a", "b", "c"]);
    expect(moveItem(["a", "b", "c"], 2, 1)).toEqual(["a", "b", "c"]);
  });

  it("手順の ID を変えると依存と分岐の行き先も付け替える", () => {
    const form = guideFormFromContent(content);
    const renamed = renameStepId(form, "reset", "reset2");
    expect(renamed.branches[0].gotoStep).toBe("reset2");
    const renamedCheck = renameStepId(form, "check", "verify");
    expect(renamedCheck.steps[1].dependsOn).toEqual(["verify"]);
    // 空の ID（新しい行）からは付け替えない。
    expect(renameStepId(form, "", "x")).toBe(form);
  });

  it("条件の ID を変えると分岐の条件も付け替える", () => {
    const form = guideFormFromContent(content);
    expect(renameConditionId(form, "account", "kind").branches[0].conditionId).toBe("kind");
  });

  it("手順を外すと、ほかの手順の依存からも外す", () => {
    const form = guideFormFromContent(content);
    const removed = removeStep(form, 0);
    expect(removed.steps.map((step) => step.id)).toEqual(["reset"]);
    expect(removed.steps[0].dependsOn).toEqual([]);
  });
});

describe("guideFormIssues（保存の前の確かめ）", () => {
  it("正しい内容は問題なし", () => {
    expect(guideFormIssues(guideFormFromContent(content))).toEqual([]);
  });

  it("必須・ID の形・選択肢・問い・分岐の値・期間を確かめる", () => {
    const form = emptyGuideForm();
    form.effectiveFrom = "2026-12-01";
    form.effectiveTo = "2026-11-01";
    form.conditions = [{ ...newConditionRow(form), id: "1bad", label: "種類", allowedValues: "A" }];
    form.branches = [{ ...newBranchRow(form), operator: "equals", values: "" }];
    const paths = guideFormIssues(form).map((issue) => issue.path);
    expect(paths).toEqual([
      "title",
      "goal.expected_result",
      "applicability.effective_to",
      "conditions[0].id",
      "conditions[0].allowed_values",
      "conditions[0].question",
      "branches[0].when.condition_id",
      "branches[0].when.values",
      "branches[0].goto_step",
    ]);
  });

  it("「いずれかに一致する」は値が 1 つ以上要る", () => {
    const form = guideFormFromContent(content);
    form.branches[0] = { ...form.branches[0], operator: "in", values: "\n" };
    expect(guideFormIssues(form).map((issue) => issue.path)).toEqual(["branches[0].when.values"]);
  });
});

describe("状態と表示", () => {
  it("アーカイブ > 下書きのみ > 未公開の変更あり > 公開中", () => {
    expect(
      supportGuideState({ status: "archived", published_revision: 2, has_unpublished_changes: false }),
    ).toBe("archived");
    expect(
      supportGuideState({ status: "active", published_revision: null, has_unpublished_changes: true }),
    ).toBe("draftOnly");
    expect(
      supportGuideState({ status: "active", published_revision: 1, has_unpublished_changes: true }),
    ).toBe("unpublished");
    expect(
      supportGuideState({ status: "active", published_revision: 1, has_unpublished_changes: false }),
    ).toBe("published");
  });

  it("検証の位置を利用者の言葉にする", () => {
    expect(issuePathLabel("steps[1].depends_on")).toBe("手順 2・依存する手順");
    expect(issuePathLabel("conditions[0]")).toBe("条件 1");
    expect(issuePathLabel("branches[2].when.values")).toBe("分岐 3・値");
    expect(issuePathLabel("goal")).toBe("目的");
    expect(issuePathLabel("title")).toBe("タイトル");
    expect(issuePathLabel("impact")).toBe("影響");
    expect(issuePathLabel("steps.0.id")).toBe("手順 1・ID");
    expect(issuePathLabel("unknown_field")).toBe("");
    expect(normalizeIssuePath("steps.10.when.values")).toBe("steps[10].when.values");
  });
});

describe("parseSupportGuideImport", () => {
  it("書き出しの形・配列・1 件を受け付ける", () => {
    expect(parseSupportGuideImport(JSON.stringify({ schema_version: 1, guides: [content] }))).toEqual({
      guides: [content],
    });
    expect(parseSupportGuideImport(JSON.stringify([content, content]))).toEqual({
      guides: [content, content],
    });
    expect(parseSupportGuideImport(JSON.stringify(content))).toEqual({ guides: [content] });
  });

  it("空・壊れた JSON・形の違い・多すぎを知らせる", () => {
    expect(parseSupportGuideImport("  ")).toEqual({ error: "supportGuides.importPanel.required" });
    expect(parseSupportGuideImport("{")).toEqual({ error: "supportGuides.importPanel.parseError" });
    expect(parseSupportGuideImport('{"items": []}')).toEqual({
      error: "supportGuides.importPanel.shapeError",
    });
    expect(parseSupportGuideImport("[]")).toEqual({ error: "supportGuides.importPanel.shapeError" });
    expect(parseSupportGuideImport(JSON.stringify(Array.from({ length: 101 }, () => content)))).toEqual({
      error: "supportGuides.importPanel.tooMany",
    });
  });
});

it("書き出しのファイル名はプロファイルと日付を含む", () => {
  expect(supportGuideExportFileName("bv/1", new Date(2026, 9, 7))).toBe(
    "support-guides-bv_1-20261007.json",
  );
});

describe("言い換え（value_aliases）", () => {
  it("「選択肢: 語、語」の行と言い換えを往復する", () => {
    const aliases = textToAliases("個別: 検証用アカウント、利用者\nグループ：部署\n壊れた行\n空: ");
    expect(aliases).toEqual({ 個別: ["検証用アカウント", "利用者"], グループ: ["部署"] });
    expect(aliasesToText(aliases)).toBe("個別: 検証用アカウント、利用者\nグループ: 部署");
    expect(textToAliases("")).toEqual({});
  });
});


describe("取込の差分と下書きで試す", () => {
  const changes: SupportGuideChange[] = [
    { section: "basic", kind: "changed", key: "", label: "題", fields: ["title", "description"] },
    { section: "steps", kind: "changed", key: "grant", label: "付与", fields: ["depends_on", "unknown"] },
    { section: "steps", kind: "added", key: "notify", label: "知らせる", fields: [] },
    { section: "impact", kind: "changed", key: "", label: "", fields: ["scope"] },
  ];

  it("節ごとにまとめ、件数と項目の名前を利用者の言葉にする", () => {
    expect(groupChangesBySection(changes).map((group) => [group.section, group.changes.length])).toEqual([
      ["basic", 1],
      ["steps", 2],
      ["impact", 1],
    ]);
    expect(countChanges(changes)).toEqual({ added: 1, removed: 0, changed: 3 });
    expect(changeSectionLabel("basic")).toBe("基本");
    expect(changeSectionLabel("steps")).toBe("手順");
    expect(changeFieldLabels(changes[0])).toEqual(["タイトル", "説明"]);
    // 分からない項目は backend の名前のまま。
    expect(changeFieldLabels(changes[1])).toEqual(["依存する手順", "unknown"]);
    expect(changeFieldLabels(changes[3])).toEqual(["影響の範囲"]);
  });

  it("試す条件の選択肢と、空の値を渡さない条件", () => {
    expect(tryConditionOptions(content.conditions[0])).toEqual(content.conditions[0].allowed_values);
    expect(tryConditionOptions({ ...content.conditions[0], type: "boolean", allowed_values: [] })).toEqual([
      "はい",
      "いいえ",
    ]);
    expect(tryConditionOptions({ ...content.conditions[0], type: "text", allowed_values: [] })).toEqual([]);
    expect(tryConditionsPayload({ target: "個別", other: "  ", note: " 承認済み " })).toEqual({
      target: "個別",
      note: "承認済み",
    });
  });
});
