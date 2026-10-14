import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  FIELD_WIDTH_REM,
  FormGrid,
  placeholderOverflows,
  SearchableMultiSelect,
  SelectField,
  TextareaField,
} from "../src";

// #1891: 幅の基準（TextareaField・SearchableMultiSelect の width、FormGrid の最大幅）と placeholder の切れの判定。

describe("placeholderOverflows", () => {
  it("1 行の欄は文字の幅が内側の幅を超えたら切れる（1px は丸めの許容）", () => {
    expect(placeholderOverflows({ lineWidths: [120], availableWidth: 120, multiline: false })).toBe(false);
    expect(placeholderOverflows({ lineWidths: [121], availableWidth: 120, multiline: false })).toBe(false);
    expect(placeholderOverflows({ lineWidths: [122], availableWidth: 120, multiline: false })).toBe(true);
  });

  it("複数行の欄は折り返した行の高さが内側の高さを超えたら切れる", () => {
    // 幅 100px に 250px の文 → 3 行 × 20px = 60px
    expect(
      placeholderOverflows({ lineWidths: [250], availableWidth: 100, multiline: true, lineHeight: 20, availableHeight: 60 })
    ).toBe(false);
    expect(
      placeholderOverflows({ lineWidths: [250], availableWidth: 100, multiline: true, lineHeight: 20, availableHeight: 40 })
    ).toBe(true);
    // 改行で分けた行も数える
    expect(
      placeholderOverflows({ lineWidths: [10, 10, 10], availableWidth: 100, multiline: true, lineHeight: 20, availableHeight: 40 })
    ).toBe(true);
  });

  it("測れない（幅 0）ときは切れていない扱い", () => {
    expect(placeholderOverflows({ lineWidths: [500], availableWidth: 0, multiline: false })).toBe(false);
  });
});

describe("幅の段（#1891）", () => {
  it("段の値はトークンと同じ", () => {
    expect(FIELD_WIDTH_REM).toEqual({ xs: 8, sm: 12, md: 20, lg: 28 });
  });

  it("TextareaField は width で段の幅になり、既定は親の幅いっぱい", () => {
    const narrow = renderToStaticMarkup(<TextareaField id="reason" label="理由" width="lg" />);
    expect(narrow).toContain("sm:w-[var(--field-width-lg)]");
    const full = renderToStaticMarkup(<TextareaField id="sql" label="SQL" />);
    expect(full).not.toContain("--field-width-");
  });

  it("SearchableMultiSelect は width で部品全体が段の幅になる", () => {
    const html = renderToStaticMarkup(
      <SearchableMultiSelect id="kb" label="ナレッジベース" options={[]} value={[]} onValueChange={() => undefined} width="lg" />
    );
    expect(html).toContain("sm:w-[var(--field-width-lg)]");
  });

  it("FormGrid はフォームの最大幅（--form-max-width）で止め、既定は 42rem 以上で 2 列", () => {
    const html = renderToStaticMarkup(
      <FormGrid>
        <span>a</span>
      </FormGrid>
    );
    expect(html).toContain("max-w-[var(--form-max-width)]");
    expect(html).toContain("@2xl:grid-cols-2");
    // 1 列のときも minmax(0, 1fr) の列（中身の最小幅で横にはみ出さない）。
    expect(html).toContain("grid-cols-1");
    expect(renderToStaticMarkup(<FormGrid columns={1} />)).not.toContain("grid-cols-2");
  });

  it("SelectField は未選択の placeholder に目印を付ける（e2e の切れの検査が見る）", () => {
    const empty = renderToStaticMarkup(
      <SelectField id="s" label="状態" value="" placeholder="状態を選択" options={[{ value: "a", label: "A" }]} onValueChange={() => undefined} />
    );
    expect(empty).toContain("data-field-placeholder");
    const chosen = renderToStaticMarkup(
      <SelectField id="s" label="状態" value="a" options={[{ value: "a", label: "A" }]} onValueChange={() => undefined} />
    );
    expect(chosen).not.toContain("data-field-placeholder");
  });
});
