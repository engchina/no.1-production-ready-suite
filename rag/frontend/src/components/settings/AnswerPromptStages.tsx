import { Disclosure, TextareaField } from "@production-ready/ui";
import { Fragment } from "react";

import type { AnswerPromptStage } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";

import { AnswerPromptCard } from "./AnswerPromptEditor";
import { groupStagesByPhase } from "./answer-prompt.logic";

/**
 * backend の順序で各段を分類し、使用条件と原文を示す。
 * 回答テンプレートの編集欄は、生成の system 指示の後に置く。
 */
export function AnswerPromptStages({ stages }: { stages: AnswerPromptStage[] }) {
  return (
    <section className="space-y-4" aria-labelledby="answer-prompt-stages-title">
      <div className="space-y-1">
        <h2 id="answer-prompt-stages-title" className="text-sm font-semibold text-fg">
          {t("settings.answerPrompts.stages.title")}
        </h2>
        <p className="text-xs leading-relaxed text-fg-muted">{t("settings.answerPrompts.stages.description")}</p>
      </div>
      {groupStagesByPhase(stages).map((group, index) => {
        const headingId = `answer-prompt-phase-${index}`;
        return (
          <section key={`${group.phase}-${index}`} className="space-y-2" aria-labelledby={headingId}>
            <h3 id={headingId} className="text-sm font-semibold text-fg">
              {t(`settings.answerPrompts.phase.${group.phase}` as I18nKey)}
            </h3>
            <p className="text-xs leading-relaxed text-fg-muted">
              {t(`settings.answerPrompts.phase.${group.phase}.description` as I18nKey)}
            </p>
            {group.stages.map((stage) => (
              <Fragment key={stage.id}>
                {stage.prompts.length > 0 ? (
                  <Disclosure
                    summary={t(`settings.answerPrompts.stage.${stage.id}` as I18nKey)}
                    description={t("settings.answerPrompts.condition", {
                      condition: t(`settings.answerPrompts.stage.${stage.id}.condition` as I18nKey),
                    })}
                    surface="sunken"
                    contentClassName="space-y-3"
                    data-testid={`answer-prompt-stage-${stage.id}`}
                  >
                    {stage.prompts.map((part) => (
                      <TextareaField
                        key={part.id}
                        id={`answer-prompt-${stage.id}-${part.id}`}
                        label={t(`settings.answerPrompts.part.${part.id}` as I18nKey)}
                        value={part.content}
                        readOnly
                        monospace
                        rows={12}
                      />
                    ))}
                  </Disclosure>
                ) : null}
                {stage.id === "generation" ? <AnswerPromptCard promptKey="vlm_answer" /> : null}
              </Fragment>
            ))}
          </section>
        );
      })}
    </section>
  );
}
