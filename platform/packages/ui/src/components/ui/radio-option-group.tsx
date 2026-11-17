import type { LucideIcon } from "lucide-react";
import { useId, type ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Fieldset } from "./field-label";
import { Skeleton } from "./skeleton";

export interface RadioOption<T extends string = string> {
  value: T;
  /** 翻訳済みの選択肢の名前（ラジオの読み上げの名前はこれだけ）。 */
  label: ReactNode;
  /**
   * 翻訳済みの説明（選択肢の名前の下に出し、ラジオの aria-describedby と結ぶ）。選べない理由などの補足や、
   * 複数の行（対応形式など）を含めてよい。
   */
  description?: ReactNode;
  /** この選択肢だけを選べなくする（準備中など）。地と名前を無効の色にし、説明は読める濃さのまま残す。 */
  disabled?: boolean;
  /** 名前の左の印（lucide のアイコン）。 */
  icon?: LucideIcon;
  /** 名前の横に並べるバッジ・チップ（既定・推奨・状態など）。説明の前に aria-describedby で結ぶ。 */
  badges?: ReactNode;
  /** 行の右の独立した操作（ボタンなど）。label の外に置き、押しても選択肢を選ばない。 */
  trailing?: ReactNode;
  /** ラジオの input に付ける data-* 属性（状態の印・e2e の目印）。 */
  inputData?: Partial<Record<`data-${string}`, string>>;
}

export interface RadioOptionGroupProps<T extends string = string> {
  /** 群の id。各ラジオの id は `${id}-${value}`。 */
  id?: string;
  /** ラジオの name（省略時は id から作る）。 */
  name?: string;
  /** 翻訳済みの見出し（legend）。 */
  legend: ReactNode;
  /** 翻訳済みの補足（legend の直下）。 */
  helper?: ReactNode;
  /** 翻訳済みのエラー（群の直下）。 */
  error?: string | null;
  required?: boolean;
  options: readonly RadioOption<T>[];
  /** 選んだ値（未選択は null）。 */
  value: T | null;
  onValueChange: (value: T) => void;
  /** 群の全体を無効にする（実行中など）。選んだ内容は読めるまま残す。 */
  disabled?: boolean;
  className?: string;
  testId?: string;
}

/** 選択肢の並び。列の数は置き場所の幅で決める（2 つの選択肢は広い画面で横に、375px では縦に並ぶ）。 */
const OPTION_GRID_CLASS =
  "grid min-w-0 max-w-[60rem] grid-cols-[repeat(auto-fit,minmax(min(100%,16rem),1fr))] gap-2";

/**
 * 1 つを選ぶラジオの群（#5340）。選択肢ごとに名前と説明を持つ行を、`Fieldset role="radiogroup"` の中に
 * ネイティブのラジオで並べる（矢印キーの移動・読み上げはブラウザに任せる）。
 * 行の高さはタッチの最小（44px）以上。列の数は置き場所の幅で決まり、狭い画面（375px）では縦に 1 列になる。
 * 選んだ行は枠と地の色に加えてラジオの印で示す（色だけに頼らない）。
 * 行には名前の左の印（`icon`）・名前の横のバッジ（`badges`）・行の右の独立した操作（`trailing`）を置ける（#5343）。
 */
export function RadioOptionGroup<T extends string = string>({
  id,
  name,
  legend,
  helper,
  error,
  required = false,
  options,
  value,
  onValueChange,
  disabled = false,
  className,
  testId,
}: RadioOptionGroupProps<T>) {
  const reactId = useId();
  const baseId = id ?? `radio-option-group-${reactId}`;
  const groupName = name ?? baseId;
  return (
    <Fieldset
      id={baseId}
      legend={legend}
      helper={helper}
      error={error}
      required={required}
      role="radiogroup"
      className={className}
    >
      {/* 行の幅は列で決まり、入力欄の幅の段（width）と補足の幅の e2e の検査（#1891）の対象外。 */}
      <div className={OPTION_GRID_CLASS} data-testid={testId} data-field-width-exempt="">
        {options.map((option) => {
          const optionId = `${baseId}-${option.value}`;
          const labelId = `${optionId}-label`;
          const badgesId = option.badges ? `${optionId}-badges` : undefined;
          const descriptionId = option.description ? `${optionId}-description` : undefined;
          const describedBy = [badgesId, descriptionId].filter(Boolean).join(" ") || undefined;
          const unavailable = option.disabled === true;
          const optionDisabled = disabled || unavailable;
          const Icon = option.icon;
          return (
            // 行の枠と地は外側に持ち、label（押すと選ぶ範囲）と行の右の操作（trailing）を並べる。
            <div
              key={option.value}
              data-surface-tint="accent"
              data-option-value={option.value}
              className={cn(
                "group/option flex min-h-[var(--control-height-touch)] min-w-0 rounded-md border border-border-control bg-surface text-sm text-fg transition-colors",
                "has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle",
                unavailable
                  ? "bg-surface-disabled"
                  : "hover:not-has-[:checked]:not-has-[:disabled]:bg-surface-hover",
                "forced-colors:has-[:checked]:border-[Highlight]"
              )}
            >
              <label
                htmlFor={optionId}
                className={cn(
                  "flex min-w-0 flex-1 cursor-pointer items-start gap-3 px-3 py-2",
                  // 無効の間も選んだ内容は読めるよう、色は変えずに押せないことだけを示す。
                  "has-[:disabled]:cursor-not-allowed"
                )}
              >
                <input
                  id={optionId}
                  type="radio"
                  name={groupName}
                  value={option.value}
                  checked={value === option.value}
                  disabled={optionDisabled}
                  aria-labelledby={labelId}
                  aria-describedby={describedBy}
                  onChange={() => onValueChange(option.value)}
                  className="mt-1 h-4 w-4 shrink-0 accent-accent-emphasis disabled:cursor-not-allowed"
                  {...option.inputData}
                />
                {Icon ? (
                  // 高さを名前の 1 行（1lh）にして、印の縦の中心を名前の 1 行目にそろえる（#2371）。
                  <span
                    className={cn(
                      "flex h-[1lh] shrink-0 items-center",
                      unavailable
                        ? "text-fg-disabled"
                        : "text-fg-muted group-has-[:checked]/option:text-accent-fg"
                    )}
                  >
                    <Icon size={20} aria-hidden />
                  </span>
                ) : null}
                <span className="grid min-w-0 flex-1 gap-0.5">
                  <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                    <span
                      id={labelId}
                      className={cn("min-w-0 break-words font-medium", unavailable && "text-fg-disabled")}
                    >
                      {option.label}
                    </span>
                    {option.badges ? (
                      <span id={badgesId} className="flex min-w-0 flex-wrap items-center gap-1.5">
                        {option.badges}
                      </span>
                    ) : null}
                  </span>
                  {option.description ? (
                    <span id={descriptionId} className="break-words text-xs leading-5 text-fg-muted">
                      {option.description}
                    </span>
                  ) : null}
                </span>
              </label>
              {option.trailing ? (
                <div className="flex shrink-0 items-start py-2 pr-3">{option.trailing}</div>
              ) : null}
            </div>
          );
        })}
      </div>
    </Fieldset>
  );
}

export interface RadioOptionGroupSkeletonProps {
  /** 翻訳済みの見出し（読み込み後と同じ文）。 */
  legend: ReactNode;
  /** 翻訳済みの補足（読み込み後と同じ文）。 */
  helper?: ReactNode;
  /** 選択肢の数（説明の無い 1 行の選択肢として場所を取る）。 */
  count: number;
  className?: string;
}

/**
 * `RadioOptionGroup` の読み込み中の形（#5343）。見出しと補足は決まった文なのでそのまま出し、選択肢を同じ並びと
 * 高さの `Skeleton` にする（読み込み後に下を押し下げない）。全体を読み上げから外すので、読み込み中であることは
 * 置く側の要素（`aria-busy` と名前）で伝える。
 */
export function RadioOptionGroupSkeleton({ legend, helper, count, className }: RadioOptionGroupSkeletonProps) {
  return (
    <div aria-hidden="true" className={className}>
      <Fieldset legend={legend} helper={helper}>
        <div className={OPTION_GRID_CLASS}>
          {Array.from({ length: Math.max(0, count) }, (_, index) => (
            <Skeleton key={index} className="min-h-[var(--control-height-touch)]" />
          ))}
        </div>
      </Fieldset>
    </div>
  );
}
