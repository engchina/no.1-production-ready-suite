// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ClarificationCard,
  ClarificationRecord,
  DEFAULT_CLARIFICATION_LABELS,
  clarificationLabels,
  clarificationRecordAnswerText,
  type ClarificationCardProps,
  type ClarificationItem,
} from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

const period: ClarificationItem = {
  id: "period",
  prompt: "どの期間の売上を見ますか？",
  reason: "「最近」の期間が質問から決められません。",
  options: [
    { id: "month", label: "今月", description: "2026 年 10 月 1 日から今日まで" },
    { id: "quarter", label: "今四半期", detail: <span data-testid="product-detail">SALES.ORDER_DATE</span> },
  ],
};
const regions: ClarificationItem = {
  id: "regions",
  prompt: "どの地域を含めますか？",
  selection: "multiple",
  options: [
    { id: "east", label: "東日本" },
    { id: "west", label: "西日本" },
  ],
};

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(props: Partial<ClarificationCardProps> = {}) {
  const onConfirm = props.onConfirm ?? vi.fn();
  act(() => root.render(<ClarificationCard items={[period]} onConfirm={onConfirm} {...props} />));
  return onConfirm;
}

function byTestId<T extends HTMLElement = HTMLElement>(id: string): T {
  const element = host.querySelector<T>(`[data-testid="${id}"]`);
  if (!element) throw new Error(`not found: ${id}`);
  return element;
}

function optionInput(itemId: string, optionId: string): HTMLInputElement {
  const input = host.querySelector<HTMLInputElement>(
    `[data-item-id="${itemId}"] [data-option-id="${optionId}"] input`
  );
  if (!input) throw new Error(`option not found: ${itemId}/${optionId}`);
  return input;
}

function otherField(itemId: string): HTMLTextAreaElement {
  const textarea = host.querySelector<HTMLTextAreaElement>(`[data-item-id="${itemId}"] textarea`);
  if (!textarea) throw new Error(`other not found: ${itemId}`);
  return textarea;
}

function type(element: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  act(() => {
    setter.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function click(element: HTMLElement) {
  act(() => element.click());
}

describe("ClarificationCard", () => {
  it("題・確認事項・確認する理由・選択肢・その他（自由入力）を出す", () => {
    render();
    const card = byTestId("clarification-card");
    expect(card.getAttribute("aria-labelledby")).toBeTruthy();
    expect(card.querySelector("h3")?.textContent).toContain("回答の前に確認させてください");
    const fieldset = card.querySelector("fieldset")!;
    expect(document.getElementById(fieldset.getAttribute("aria-labelledby")!)?.textContent).toBe(period.prompt);
    expect(document.getElementById(fieldset.getAttribute("aria-describedby")!)?.textContent).toContain(
      "確認する理由"
    );
    expect(optionInput("period", "month").type).toBe("radio");
    expect(card.textContent).toContain("2026 年 10 月 1 日から今日まで");
    expect(byTestId("product-detail").textContent).toBe("SALES.ORDER_DATE");
    // 「その他（自由入力）」は選択肢があっても常に出す。
    expect(card.querySelector(`label[for="${otherField("period").id}"]`)?.textContent).toContain("その他（自由入力）");
  });

  it("答えるまで確定を押せず、押せない理由を aria-describedby で結ぶ", () => {
    render();
    const confirm = byTestId<HTMLButtonElement>("clarification-card-confirm");
    expect(confirm.disabled).toBe(true);
    const blocked = byTestId("clarification-card-blocked");
    expect(confirm.getAttribute("aria-describedby")).toBe(blocked.id);
    expect(blocked.textContent).toBe(DEFAULT_CLARIFICATION_LABELS.confirmBlocked);

    click(optionInput("period", "month"));
    expect(confirm.disabled).toBe(false);
    expect(confirm.getAttribute("aria-describedby")).toBeNull();
    expect(host.querySelector('[data-testid="clarification-card-blocked"]')).toBeNull();
  });

  it("1 つだけ選ぶ確認事項では、選択肢と自由入力はどちらか一方", () => {
    const onConfirm = render();
    click(optionInput("period", "month"));
    type(otherField("period"), "  先月の 15 日から  ");
    expect(optionInput("period", "month").checked).toBe(false);
    click(byTestId("clarification-card-confirm"));
    expect(onConfirm).toHaveBeenLastCalledWith([
      { itemId: "period", optionIds: [], otherText: "先月の 15 日から" },
    ]);

    click(optionInput("period", "quarter"));
    expect(otherField("period").value).toBe("");
    click(byTestId("clarification-card-confirm"));
    expect(onConfirm).toHaveBeenLastCalledWith([{ itemId: "period", optionIds: ["quarter"], otherText: "" }]);
  });

  it("空白だけの自由入力は答えにしない", () => {
    render();
    type(otherField("period"), "   ");
    expect(byTestId<HTMLButtonElement>("clarification-card-confirm").disabled).toBe(true);
  });

  it("複数選ぶ確認事項はチェックボックスで、自由入力と併せて答えられる", () => {
    const onConfirm = render({ items: [regions] });
    expect(optionInput("regions", "east").type).toBe("checkbox");
    expect(host.textContent).toContain(DEFAULT_CLARIFICATION_LABELS.multipleHint);
    click(optionInput("regions", "east"));
    click(optionInput("regions", "west"));
    click(optionInput("regions", "east"));
    type(otherField("regions"), "沖縄");
    click(byTestId("clarification-card-confirm"));
    expect(onConfirm).toHaveBeenLastCalledWith([{ itemId: "regions", optionIds: ["west"], otherText: "沖縄" }]);
  });

  it("複数の確認事項は、すべてに答えると確定を押せ、残りの件数を理由に出す", () => {
    render({ items: [period, regions] });
    expect(byTestId("clarification-card-meta").textContent).toBe("確認事項 2 件");
    expect(byTestId("clarification-card-blocked").textContent).toContain("残り 2 件");
    click(optionInput("period", "month"));
    expect(byTestId("clarification-card-blocked").textContent).toContain("残り 1 件");
    click(optionInput("regions", "west"));
    expect(byTestId<HTMLButtonElement>("clarification-card-confirm").disabled).toBe(false);
  });

  it("スキップ・キャンセルは渡したときだけ出し、補足を説明として結ぶ", () => {
    const onSkip = vi.fn();
    const onCancel = vi.fn();
    render();
    expect(host.querySelector('[data-testid="clarification-card-skip"]')).toBeNull();
    expect(host.querySelector('[data-testid="clarification-card-cancel"]')).toBeNull();

    render({ onSkip, onCancel });
    const skip = byTestId<HTMLButtonElement>("clarification-card-skip");
    const cancel = byTestId<HTMLButtonElement>("clarification-card-cancel");
    expect(skip.textContent).toBe("確認せずに回答");
    expect(document.getElementById(skip.getAttribute("aria-describedby")!)?.textContent).toContain(
      "前提を示して回答します。"
    );
    expect(cancel.textContent).toBe("確認をやめる");
    expect(document.getElementById(cancel.getAttribute("aria-describedby")!)?.textContent).toContain(
      "質問は入力欄に戻ります。"
    );
    // 答える前でもスキップ・キャンセルは押せる。
    click(skip);
    click(cancel);
    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("処理中は押したボタンだけを loading にし、ほかの操作と入力を押せなくする", () => {
    const onConfirm = vi.fn();
    render({ onConfirm, onSkip: vi.fn(), onCancel: vi.fn(), defaultValue: { period: { optionIds: ["month"], otherText: "" } } });
    render({
      onConfirm,
      onSkip: vi.fn(),
      onCancel: vi.fn(),
      busyAction: "confirm",
    });
    const confirm = byTestId<HTMLButtonElement>("clarification-card-confirm");
    expect(confirm.getAttribute("aria-busy")).toBe("true");
    expect(confirm.disabled).toBe(false);
    expect(byTestId<HTMLButtonElement>("clarification-card-skip").disabled).toBe(true);
    expect(byTestId<HTMLButtonElement>("clarification-card-cancel").disabled).toBe(true);
    expect(optionInput("period", "month").disabled).toBe(true);
    expect(otherField("period").disabled).toBe(true);
    // 選んだ内容は処理中も残る。
    expect(optionInput("period", "month").checked).toBe(true);
    click(confirm);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("位置と往復を出し、上限の回では最後の確認であることを添える", () => {
    render({ progress: { current: 2, total: 3 }, round: { current: 1, max: 2 } });
    expect(byTestId("clarification-card-meta").textContent).toBe("確認事項 2 / 3・確認 1 回目（上限 2 回）");
    expect(host.querySelector('[data-testid="clarification-card-last-round"]')).toBeNull();
    render({ progress: { current: 2, total: 3 }, round: { current: 2, max: 2 } });
    expect(byTestId("clarification-card-last-round").textContent).toContain("これが最後の確認です。");
  });

  it("確認事項が変わったら答えを空にし、題へフォーカスを移す", () => {
    render();
    click(optionInput("period", "month"));
    const next: ClarificationItem = { ...regions, id: "regions-2" };
    act(() => root.render(<ClarificationCard items={[next]} onConfirm={vi.fn()} />));
    expect(optionInput("regions-2", "east").checked).toBe(false);
    expect(document.activeElement).toBe(host.querySelector("h3"));
  });

  it("autoFocus がなければ最初に出たときはフォーカスを動かさない", () => {
    render();
    expect(document.activeElement).not.toBe(host.querySelector("h3"));
    act(() => root.unmount());
    root = createRoot(host);
    render({ autoFocus: true });
    expect(document.activeElement).toBe(host.querySelector("h3"));
  });

  it("受控の答えを使い、変更を onValueChange に渡す", () => {
    const onValueChange = vi.fn();
    render({ value: { period: { optionIds: ["quarter"], otherText: "" } }, onValueChange });
    expect(optionInput("period", "quarter").checked).toBe(true);
    click(optionInput("period", "month"));
    expect(onValueChange).toHaveBeenLastCalledWith({ period: { optionIds: ["month"], otherText: "" } });
    // 受控なので親が値を変えるまで表示は変わらない。
    expect(optionInput("period", "quarter").checked).toBe(true);
  });

  it("製品が決めた押せない理由を優先する", () => {
    render({ blockedReason: "直しを送ってから生成してください。", defaultValue: { period: { optionIds: ["month"], otherText: "" } } });
    const confirm = byTestId<HTMLButtonElement>("clarification-card-confirm");
    expect(confirm.disabled).toBe(true);
    expect(document.getElementById(confirm.getAttribute("aria-describedby")!)?.textContent).toBe(
      "直しを送ってから生成してください。"
    );
  });

  it("答えなくてよい確認事項は押せない理由に数えない", () => {
    const onConfirm = render({ items: [{ ...period, optional: true }] });
    const confirm = byTestId<HTMLButtonElement>("clarification-card-confirm");
    expect(confirm.disabled).toBe(false);
    click(confirm);
    expect(onConfirm).toHaveBeenLastCalledWith([{ itemId: "period", optionIds: [], otherText: "" }]);
    render({ items: [{ ...period, optional: true }, regions] });
    expect(byTestId("clarification-card-blocked").textContent).toContain("残り 1 件");
  });

  it("製品の操作を確定の右に置き、空の補足は出さない", () => {
    render({
      onSkip: vi.fn(),
      onCancel: vi.fn(),
      actions: <button type="button" data-testid="product-action">直しを伝える</button>,
      labels: { cancel: "直すのをやめる", cancelHint: "" },
    });
    const buttons = Array.from(host.querySelectorAll("button")).map((button) => button.textContent);
    expect(buttons).toEqual(["この内容で回答", "直しを伝える", "確認せずに回答", "直すのをやめる"]);
    expect(byTestId("clarification-card-cancel").getAttribute("aria-describedby")).toBeNull();
    expect(host.textContent).not.toContain("質問は入力欄に戻ります。");
  });

  it("製品の名詞で文言を組む（NL2SQL）", () => {
    const labels = clarificationLabels({ stage: "SQL の生成", action: "SQL を生成" });
    expect(labels.title).toBe("SQL の生成の前に確認させてください");
    expect(labels.confirm).toBe("この内容で SQL を生成");
    expect(labels.skip).toBe("確認せずに SQL を生成");
    expect(labels.skipHint).toBe("前提を示して SQL を生成します。");
    expect(labels.recordTitle).toBe("SQL の生成前に確認した内容");
    expect(DEFAULT_CLARIFICATION_LABELS.confirm).toBe("この内容で回答");
    expect(DEFAULT_CLARIFICATION_LABELS.recordTitle).toBe("回答前に確認した内容");
  });
});

describe("ClarificationRecord", () => {
  const entries = [
    { id: "period", prompt: period.prompt, selected: ["今月"] },
    { id: "regions", prompt: regions.prompt, selected: ["東日本", "西日本"], otherText: "沖縄" },
    { id: "unit", prompt: "単位は？", selected: [] },
  ];

  it("会話の利用者の側に、確認事項と答えを残す", () => {
    act(() => root.render(<ClarificationRecord entries={entries} />));
    const record = byTestId("clarification-record");
    expect(record.dataset.outcome).toBe("answered");
    const group = record.querySelector('[role="group"]')!;
    expect(document.getElementById(group.getAttribute("aria-labelledby")!)?.textContent).toBe("回答前に確認した内容");
    const answers = Array.from(record.querySelectorAll("dd")).map((dd) => dd.textContent);
    expect(answers).toEqual(["今月", "東日本、西日本、その他: 沖縄", "（答えなし）"]);
  });

  it("スキップしたことを残す", () => {
    act(() => root.render(<ClarificationRecord entries={entries.slice(0, 1)} outcome="skipped" />));
    expect(byTestId("clarification-record-skipped").textContent).toBe("確認せずに回答しました（前提を示して回答）");
    expect(host.querySelectorAll("dd")).toHaveLength(0);
  });

  it("回答の詳細の折りたたみとしても出せる", () => {
    act(() => root.render(<ClarificationRecord entries={entries} variant="disclosure" />));
    const details = byTestId("clarification-record");
    expect(details.tagName).toBe("DETAILS");
    expect(details.querySelector("summary")?.textContent).toContain("回答前に確認した内容");
    expect((details as HTMLDetailsElement).open).toBe(false);
  });

  it("答えの文を組む", () => {
    expect(clarificationRecordAnswerText(entries[1])).toBe("東日本、西日本、その他: 沖縄");
    expect(clarificationRecordAnswerText({ id: "x", prompt: "?", selected: [], otherText: "  " })).toBe("（答えなし）");
  });
});
