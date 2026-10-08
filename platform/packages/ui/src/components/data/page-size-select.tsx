import { useId } from "react";

import { SelectField } from "../ui/select-field";
import type { ControlSize, FieldWidth } from "../ui/control-size";
import { DEFAULT_PAGE_SIZE } from "./pagination";

/** 1 ページの件数の既定の選択肢（既定は共通の 10 件）。 */
export const PAGE_SIZE_OPTIONS = [DEFAULT_PAGE_SIZE, 50, 100] as const;

export interface PageSizeSelectLabels {
  /** 欄のラベル（既定「1 ページの件数」）。 */
  label: string;
  /** 選択肢の文言（既定「{n} 件」）。 */
  option: (size: number) => string;
}

export const DEFAULT_PAGE_SIZE_SELECT_LABELS: PageSizeSelectLabels = {
  label: "1 ページの件数",
  option: (size) => `${size} 件`,
};

export interface PageSizeSelectProps {
  id?: string;
  value: number;
  onValueChange: (size: number) => void;
  /** 選択肢（既定 10 / 50 / 100）。 */
  options?: readonly number[];
  labels?: Partial<PageSizeSelectLabels>;
  size?: ControlSize;
  /** 欄の幅（既定 `sm`。値とラベルが 1 行に収まる。#613）。 */
  width?: FieldWidth;
  /** 表の行など、ラベルを見せない所（読み上げには残る）。 */
  labelHidden?: boolean;
  disabled?: boolean;
  className?: string;
}

/**
 * 1 ページの件数の選択（#1266。`ResultTable`・RAG のフィードバック・分析の一覧で同じ部品にする）。
 * 値（「50 件」）とラベルが 1 行に収まる `sm` の幅（#613）。選択肢に無い値を渡されても、その値を選択肢に足して出す。
 */
export function PageSizeSelect({
  id,
  value,
  onValueChange,
  options = PAGE_SIZE_OPTIONS,
  labels: labelOverrides,
  size,
  width = "sm",
  labelHidden,
  disabled,
  className,
}: PageSizeSelectProps) {
  const reactId = useId();
  const labels = { ...DEFAULT_PAGE_SIZE_SELECT_LABELS, ...labelOverrides };
  const sizes = options.includes(value) ? options : [...options, value].sort((a, b) => a - b);
  return (
    <SelectField
      id={id ?? `page-size-${reactId}`}
      label={labels.label}
      value={String(value)}
      options={sizes.map((option) => ({ value: String(option), label: labels.option(option) }))}
      onValueChange={(next) => onValueChange(Number(next))}
      width={width}
      size={size}
      labelHidden={labelHidden}
      disabled={disabled}
      className={className}
    />
  );
}
