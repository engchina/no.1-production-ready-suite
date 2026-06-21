import { Link } from "react-router-dom";
import {
  BookOpen,
  Bot,
  Database,
  FileSpreadsheet,
  FlaskConical,
  History,
  MessageSquareText,
  ShieldCheck,
  Sparkles,
  Table2,
} from "lucide-react";

import { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, StatusBadge } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

const featureGroups = [
  {
    titleKey: "dashboard.group.core",
    items: [
      { labelKey: "nav.query", href: APP_ROUTES.query, icon: Sparkles },
      { labelKey: "nav.schema", href: APP_ROUTES.schema, icon: Table2 },
      { labelKey: "nav.profiles", href: APP_ROUTES.profiles, icon: Database },
    ],
  },
  {
    titleKey: "dashboard.group.learning",
    items: [
      { labelKey: "nav.glossaryRules", href: APP_ROUTES.glossaryRules, icon: BookOpen },
      { labelKey: "nav.learning", href: APP_ROUTES.learning, icon: MessageSquareText },
      { labelKey: "nav.sqlAnalysis", href: APP_ROUTES.sqlAnalysis, icon: ShieldCheck },
    ],
  },
  {
    titleKey: "dashboard.group.ops",
    items: [
      { labelKey: "nav.dataTools", href: APP_ROUTES.dataTools, icon: FileSpreadsheet },
      { labelKey: "nav.evaluation", href: APP_ROUTES.evaluation, icon: FlaskConical },
      { labelKey: "nav.engineOperations", href: APP_ROUTES.engineOperations, icon: Bot },
      { labelKey: "nav.history", href: APP_ROUTES.history, icon: History },
    ],
  },
] as const;

const absorptionItems = [
  {
    titleKey: "dashboard.absorb.engines",
    descriptionKey: "dashboard.absorb.engines.desc",
    href: APP_ROUTES.query,
  },
  {
    titleKey: "dashboard.absorb.denpyo",
    descriptionKey: "dashboard.absorb.denpyo.desc",
    href: APP_ROUTES.query,
  },
  {
    titleKey: "dashboard.absorb.schema",
    descriptionKey: "dashboard.absorb.schema.desc",
    href: APP_ROUTES.schema,
  },
  {
    titleKey: "dashboard.absorb.rules",
    descriptionKey: "dashboard.absorb.rules.desc",
    href: APP_ROUTES.glossaryRules,
  },
  {
    titleKey: "dashboard.absorb.learning",
    descriptionKey: "dashboard.absorb.learning.desc",
    href: APP_ROUTES.learning,
  },
  {
    titleKey: "dashboard.absorb.analysis",
    descriptionKey: "dashboard.absorb.analysis.desc",
    href: APP_ROUTES.sqlAnalysis,
  },
  {
    titleKey: "dashboard.absorb.evaluation",
    descriptionKey: "dashboard.absorb.evaluation.desc",
    href: APP_ROUTES.evaluation,
  },
  {
    titleKey: "dashboard.absorb.ops",
    descriptionKey: "dashboard.absorb.ops.desc",
    href: APP_ROUTES.engineOperations,
  },
] as const;

export function DashboardPage() {
  return (
    <>
      <PageHeader title={t("nav.dashboard")} subtitle={t("page.dashboard.subtitle")} />
      <main className="grid gap-5 p-4 lg:p-8">
        <div className="grid gap-3 md:grid-cols-4">
          <Metric label={t("dashboard.metric.engines")} value="3" />
          <Metric label={t("dashboard.metric.safety")} value="SELECT/WITH" />
          <Metric label={t("dashboard.metric.feedback")} value={t("dashboard.metric.enabled")} />
          <Metric label={t("dashboard.metric.assets")} value={t("dashboard.metric.enabled")} />
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          {featureGroups.map((group) => (
            <Card key={group.titleKey}>
              <CardHeader>
                <CardTitle>{t(group.titleKey)}</CardTitle>
                <CardDescription>{t("dashboard.group.status")}</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-2">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      to={item.href}
                      className="flex min-h-12 items-center justify-between gap-3 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-900 transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-sky-200"
                    >
                      <span className="flex min-w-0 items-center gap-2">
                        <Icon size={16} className="shrink-0 text-slate-500" aria-hidden="true" />
                        <span className="truncate">{t(item.labelKey)}</span>
                      </span>
                      <StatusBadge variant="success" label={t("dashboard.status.ready")} />
                    </Link>
                  );
                })}
              </CardContent>
            </Card>
          ))}
        </div>

        <Card>
          <CardHeader>
            <CardTitle>{t("dashboard.absorb.title")}</CardTitle>
            <CardDescription>{t("dashboard.absorb.description")}</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 md:grid-cols-2">
            {absorptionItems.map((item) => (
              <Link
                key={item.titleKey}
                to={item.href}
                className="grid min-h-20 gap-2 rounded-md border border-slate-200 bg-white px-3 py-3 text-sm transition hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-sky-200"
              >
                <span className="flex items-start justify-between gap-3">
                  <span className="font-semibold text-slate-900">{t(item.titleKey)}</span>
                  <StatusBadge variant="success" label={t("dashboard.status.ready")} />
                </span>
                <span className="text-sm leading-6 text-slate-600">{t(item.descriptionKey)}</span>
              </Link>
            ))}
          </CardContent>
        </Card>
      </main>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-slate-50 p-4">
      <p className="text-xs font-medium text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900">{value}</p>
    </div>
  );
}
