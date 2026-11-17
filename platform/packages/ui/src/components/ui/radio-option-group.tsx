import { useId, type ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Fieldset } from "./field-label";

export interface RadioOption<T extends string = string> {
  value: T;
  /** 翻訳済みの選択肢の名前。 */
  label: ReactNode;
  /** 翻訳済みの 1 行の説明（選択肢の名前の下に出し、ラジオの aria-describedby と結ぶ）。 */
  description?: ReactNode;
  disabled?: boolean;
}

export interface RadioOptionGroupProps<T extends string = string> {
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

/**
 * 1 つを選ぶラジオの群（#5340）。選択肢ごとに名前と 1 行の説明を持つ行を、`Fieldset role="radiogroup"` の中に
 * ネイティブのラジオで並べる（矢印キーの移動・読み上げはブラウザに任せる）。
 * 行の高さはタッチの最小（44px）以上。列の数は置き場所の幅で決まり、狭い画面（375px）では縦に 1 列になる。
 * 選んだ行は枠と地の色に加えてラジオの印で示す（色だけに頼らない）。
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
      {/* 列の数は置き場所の幅で決める（2 つの選択肢は広い画面で横に、375px では縦に並ぶ）。 */}
      <div
        className="grid min-w-0 max-w-[60rem] grid-cols-[repeat(auto-fit,minmax(min(100%,16rem),1fr))] gap-2"
        data-testid={testId}
      >
        {options.map((option) => {
          const optionId = `${baseId}-${option.value}`;
          const labelId = `${optionId}-label`;
          const descriptionId = option.description ? `${optionId}-description` : undefined;
          const optionDisabled = disabled || option.disabled === true;
          return (
            <label
              key={option.value}
              htmlFor={optionId}
              data-surface-tint="accent"
              data-option-value={option.value}
              className={cn(
                "flex min-h-[var(--control-height-touch)] min-w-0 cursor-pointer items-start gap-3 rounded-md border border-border-control bg-surface px-3 py-2 text-sm text-fg transition-colors",
                "hover:not-has-[:checked]:not-has-[:disabled]:bg-surface-hover has-[:checked]:border-accent-emphasis has-[:checked]:bg-accent-subtle",
                // 無効の間も選んだ内容は読めるよう、色は変えずに押せないことだけを示す。
                "has-[:disabled]:cursor-not-allowed",
                "forced-colors:has-[:checked]:border-[Highlight]"
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
                aria-describedby={descriptionId}
                onChange={() => onValueChange(option.value)}
                className="mt-1 h-4 w-4 shrink-0 accent-accent-emphasis disabled:cursor-not-allowed"
              />
              <span className="grid min-w-0 gap-0.5">
                <span id={labelId} className="break-words font-medium">
                  {option.label}
                </span>
                {option.description ? (
                  <span id={descriptionId} className="break-words text-xs leading-5 text-fg-muted">
                    {option.description}
                  </span>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>
    </Fieldset>
  );
}
