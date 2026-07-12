import { ToggleChip, type ThemePreference } from "@engchina/production-ready-ui";

import { PageHeader } from "@/components/PageHeader";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { t } from "@/lib/i18n";
import { useUiStore } from "@/lib/ui-store";

const THEME_OPTIONS: Array<{ value: ThemePreference; labelKey: string }> = [
  { value: "light", labelKey: "appearance.theme.light" },
  { value: "dark", labelKey: "appearance.theme.dark" },
  { value: "system", labelKey: "appearance.theme.system" },
];

/** 外観設定（配色テーマ）。ライト/ダーク/自動(OS 追従) を切り替える。既定はライト。 */
export function AppearanceSettings() {
  const theme = useUiStore((state) => state.theme);
  const setTheme = useUiStore((state) => state.setTheme);

  return (
    <>
      <PageHeader title={t("nav.settingsAppearance")} subtitle={t("appearance.subtitle")} />
      <main className="grid gap-4 p-4 lg:p-8">
        <Card>
          <CardHeader>
            <CardTitle>{t("appearance.theme.label")}</CardTitle>
            <CardDescription>{t("appearance.theme.hint")}</CardDescription>
          </CardHeader>
          <CardContent>
            <div
              role="group"
              aria-label={t("appearance.theme.label")}
              className="flex flex-wrap gap-2"
              data-testid="appearance-theme-toggle"
            >
              {THEME_OPTIONS.map((option) => (
                <ToggleChip
                  key={option.value}
                  selected={theme === option.value}
                  onClick={() => setTheme(option.value)}
                >
                  {t(option.labelKey)}
                </ToggleChip>
              ))}
            </div>
          </CardContent>
        </Card>
      </main>
    </>
  );
}
