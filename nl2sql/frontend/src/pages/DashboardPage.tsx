import { PageHeader, Card, CardHeader, CardTitle, CardDescription, CardContent, EmptyState } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";

export function DashboardPage() {
  return (
    <>
      <PageHeader title={t("nav.dashboard")} subtitle={t("page.dashboard.subtitle")} />
      <div className="grid gap-4 p-8 md:grid-cols-3">
        {[t("nav.query"), t("nav.history"), t("nav.evaluation")].map((title) => (
          <Card key={title}>
            <CardHeader>
              <CardTitle>{title}</CardTitle>
              <CardDescription>{t("common.empty.hint")}</CardDescription>
            </CardHeader>
            <CardContent>
              <EmptyState title={t("common.empty.title")} hint={t("common.empty.hint")} />
            </CardContent>
          </Card>
        ))}
      </div>
    </>
  );
}
