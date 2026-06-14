import { Link } from "react-router-dom";
import {
  ArrowRight,
  FileSearch,
  FileStack,
  Upload,
  type LucideIcon,
} from "lucide-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { APP_ROUTES } from "@/lib/routes";
import { t, type I18nKey } from "@/lib/i18n";

interface Feature {
  href: string;
  labelKey: I18nKey;
  descKey: I18nKey;
  icon: LucideIcon;
}

const FEATURES: Feature[] = [
  {
    href: APP_ROUTES.upload,
    labelKey: "nav.upload",
    descKey: "dashboard.feature.upload.description",
    icon: Upload,
  },
  {
    href: APP_ROUTES.fileList,
    labelKey: "nav.fileList",
    descKey: "dashboard.feature.fileList.description",
    icon: FileStack,
  },
  {
    href: APP_ROUTES.search,
    labelKey: "nav.search",
    descKey: "dashboard.feature.search.description",
    icon: FileSearch,
  },
];

/** 主要機能ハブ：各機能への導線カード。 */
export function FeatureHub() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("dashboard.featureHub.title")}</CardTitle>
        <CardDescription>{t("dashboard.featureHub.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => {
            const Icon = f.icon;
            return (
              <Link
                key={f.href}
                to={f.href}
                className="group flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-background p-4 transition-colors hover:border-primary hover:bg-info-bg/40"
              >
                <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <Icon size={18} aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1 text-sm font-semibold text-foreground">
                    {t(f.labelKey)}
                    <ArrowRight
                      size={14}
                      className="opacity-0 transition-opacity group-hover:opacity-100"
                      aria-hidden
                    />
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted">
                    {t(f.descKey)}
                  </span>
                </span>
              </Link>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
