import { ChevronRight, FileSearch, Sparkles, Upload, type LucideIcon } from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t, type I18nKey } from "@/lib/i18n";

interface Step {
  labelKey: I18nKey;
  icon: LucideIcon;
}

const STEPS: Step[] = [
  { labelKey: "dashboard.workflow.step.upload", icon: Upload },
  { labelKey: "dashboard.workflow.step.ingest", icon: Sparkles },
  { labelKey: "dashboard.workflow.step.search", icon: FileSearch },
];

/** 業務フロー：推奨作業順のステッパー。 */
export function WorkflowSteps() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.workflow.title")}</CardTitle>
        <CardDescription>{t("dashboard.workflow.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ol className="flex flex-wrap items-center gap-y-3">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            return (
              <li key={s.labelKey} className="flex items-center">
                <div className="flex items-center gap-2">
                  <span className="flex size-8 items-center justify-center rounded-full bg-primary/10 text-primary">
                    <Icon size={16} aria-hidden />
                  </span>
                  <span className="text-sm font-medium text-foreground">
                    <span className="tnum mr-1 text-xs text-muted">{i + 1}.</span>
                    {t(s.labelKey)}
                  </span>
                </div>
                {i < STEPS.length - 1 ? (
                  <ChevronRight size={16} className="mx-2 text-muted" aria-hidden />
                ) : null}
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}
