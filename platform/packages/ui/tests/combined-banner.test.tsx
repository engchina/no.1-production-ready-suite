import { FileKey } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  Banner,
  CombinedBanner,
  PreconditionBanner,
  ProcessFailureNotice,
  combinedBannerItems,
  preconditionBannerItem,
  processFailureBannerItem,
} from "../src";

// 同じ区画の案内は 1 つの枠（messaging.md §3.4.3）。
describe("CombinedBanner", () => {
  it("複数の項目を 1 つの枠にまとめ、色・role・見出しを最も重い項目のものにする", () => {
    const html = renderToStaticMarkup(
      <CombinedBanner
        testId="notice"
        items={[
          { id: "readonly", severity: "info", children: "閲覧だけできます。" },
          { id: "schema", severity: "warning", title: "更新があります", children: "「作成・更新」で更新します。" },
          { id: "failure", severity: "danger", title: "前回の操作が失敗しました", children: "再試行してください。" },
        ]}
      />
    );
    // 枠は 1 つ。role は外側の 1 か所だけ（項目には付けない）。
    expect(html.match(/role="/g)).toHaveLength(1);
    expect(html).toMatch(/^<div role="alert" data-surface-tint="danger" data-testid="notice"/);
    expect(html).toContain("border-danger-border bg-danger-subtle");
    expect(html).toContain("divide-danger-border");
    // 項目は重い順。最も重い項目の見出しが先頭。
    const order = ["前回の操作が失敗しました", "更新があります", "閲覧だけできます。"].map((text) => html.indexOf(text));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html.match(/<li /g)).toHaveLength(3);
    // 各項目の文は 1 回だけ。
    expect(html.split("再試行してください。")).toHaveLength(2);
  });

  it("同じ重さは渡した順のまま並べ、warning の枠は status にする", () => {
    const html = renderToStaticMarkup(
      <CombinedBanner
        items={[
          { id: "a", severity: "warning", title: "A の案内", testId: "item-a" },
          { id: "b", severity: "info", title: "B の案内" },
          { id: "c", severity: "warning", title: "C の案内", action: <button type="button">開く</button> },
        ]}
      />
    );
    expect(html).toMatch(/^<div role="status" data-surface-tint="warning"/);
    expect(html.indexOf("A の案内")).toBeLessThan(html.indexOf("C の案内"));
    expect(html.indexOf("C の案内")).toBeLessThan(html.indexOf("B の案内"));
    expect(html).toContain('<li data-testid="item-a"');
    // 操作はその項目の中に出す。
    expect(html.indexOf("開く")).toBeGreaterThan(html.indexOf("C の案内"));
    expect(html.indexOf("開く")).toBeLessThan(html.indexOf("B の案内"));
  });

  it("項目が 1 つなら Banner と同じ形にし、出す項目が無ければ描かない", () => {
    const single = renderToStaticMarkup(
      <CombinedBanner items={[false, null, { id: "only", severity: "warning", title: "見出し", children: "本文" }]} />
    );
    expect(single).not.toContain("<ul");
    const banner = renderToStaticMarkup(
      <Banner severity="warning" title="見出し">
        本文
      </Banner>
    );
    // 単独の項目は項目の要素で包むだけで、中身は Banner と同じ。
    expect(single.replace(/<div>/, "").replace(/<\/div><\/div>$/, "</div>")).toBe(banner);

    expect(renderToStaticMarkup(<CombinedBanner items={[]} />)).toBe("");
    expect(renderToStaticMarkup(<CombinedBanner items={[undefined, { id: "empty", severity: "danger" }]} />)).toBe("");
  });

  it("combinedBannerItems は空の項目を捨て、重い順に並べる", () => {
    expect(
      combinedBannerItems([
        { id: "i", severity: "info", title: "i" },
        { id: "empty", severity: "danger" },
        false,
        { id: "w", severity: "warning", children: "w" },
      ]).map((item) => item.id)
    ).toEqual(["w", "i"]);
  });

  // 失敗のカード（§9.1）と前提の案内（§3.4.2）も同じ区画の枠の項目にできる（#5226）。
  it("失敗のカードを項目にすると、枠は danger の alert 1 つで、ほかの案内と区切り線で並ぶ", () => {
    const html = renderToStaticMarkup(
      <CombinedBanner
        testId="notice"
        items={[
          { id: "outcome", severity: "warning", title: "書き込みの結果が不明です", testId: "outcome" },
          processFailureBannerItem({
            failure: {
              summary: "「ツールの実行」で処理が止まりました。",
              cause: "接続先が応答しませんでした。",
              nextAction: "もう一度質問してください。",
              errorCode: "E1",
            },
            action: <button type="button">もう一度送信</button>,
            testId: "failure",
          }),
        ]}
      />
    );
    expect(html.match(/role="/g)).toHaveLength(1);
    expect(html).toMatch(/^<div role="alert" data-surface-tint="danger" data-testid="notice"/);
    expect(html.match(/<li /g)).toHaveLength(2);
    // 失敗（重い方）が先頭。見出し → 原因 → 次の操作 →「詳細」（失敗は開く）→ 操作の順は単独のカードと同じ。
    const order = ["「ツールの実行」で処理が止まりました。", "接続先が応答しませんでした。", "もう一度質問してください。", "E1", "もう一度送信", "書き込みの結果が不明です"].map((text) => html.indexOf(text));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    expect(html).toContain('data-testid="failure"');
    expect(html).toMatch(/<details[^>]* open=""/);
  });

  it("単独の失敗のカードは項目と同じ内容を 1 つの Banner で描く", () => {
    const failure = { summary: "回答の保存で失敗しました。", cause: "保存できませんでした。" };
    const alone = renderToStaticMarkup(<ProcessFailureNotice failure={failure} severity="warning" testId="f" />);
    const item = renderToStaticMarkup(<CombinedBanner items={[processFailureBannerItem({ failure, severity: "warning" })]} />);
    expect(alone).toMatch(/^<div class="min-w-0" data-testid="f" data-process-failure="warning"><div role="status"/);
    // 同じ関数が内容を作るので、見出し・本文・「詳細」（warning は閉じる）の文字は項目と同じ。
    const text = (html: string) => html.replace(/<[^>]+>/g, "");
    expect(text(alone)).toBe(text(item));
    expect(alone).not.toMatch(/<details[^>]* open=""/);
  });

  it("前提の案内を項目にすると、id が項目の要素に付き、導線 → 再試行の順を保つ", () => {
    const html = renderToStaticMarkup(
      <CombinedBanner
        items={[
          { id: "note", severity: "info", children: "判定は参考の値です。" },
          preconditionBannerItem({
            id: "start-precondition",
            testId: "precondition",
            title: "資格情報が作成されていません",
            message: "評価には資格情報が必要です。",
            settingsLink: { to: "/settings/credential", label: "資格情報を開く", icon: FileKey },
            onRetry: () => undefined,
          }),
        ]}
      />
    );
    expect(html.match(/role="/g)).toHaveLength(1);
    expect(html).toMatch(/^<div role="status" data-surface-tint="warning"/);
    expect(html).toContain('<li id="start-precondition" data-testid="precondition"');
    const order = ["資格情報が作成されていません", "評価には資格情報が必要です。", "資格情報を開く", "再試行", "判定は参考の値です。"].map((text) => html.indexOf(text));
    expect(order.every((index) => index >= 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
    // 単独の案内は今までどおり外側の要素に id と testId を持つ。
    const alone = renderToStaticMarkup(
      <PreconditionBanner id="p" testId="t" title="資格情報が作成されていません" message="評価には資格情報が必要です。" />
    );
    expect(alone).toMatch(/^<div id="p" data-testid="t"><div role="status"/);
  });
});
