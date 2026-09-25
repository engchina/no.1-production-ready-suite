import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageBody,
  PageHeader,
  type ThemePreference,
} from "@engchina/production-ready-ui";

import { APPEARANCE_MESSAGES, type AppearanceMessages } from "./messages";

const THEME_OPTIONS: Array<{ value: ThemePreference; label: keyof AppearanceMessages }> = [
  { value: "light", label: "themeLight" },
  { value: "dark", label: "themeDark" },
  { value: "system", label: "themeSystem" },
];

export interface AppearanceSettingsPageProps {
  /** 現在のテーマ選好（各製品の `useUiStore((s) => s.theme)`）。 */
  theme: ThemePreference;
  /** テーマ選好を変更する（各製品の `useUiStore((s) => s.setTheme)`）。反映は `initTheme` が行う。 */
  onThemeChange: (theme: ThemePreference) => void;
  /** 既定の文言の上書き（各製品の i18n から渡す）。 */
  messages?: Partial<AppearanceMessages>;
}

/** 外観設定（配色テーマ）。ライト / ダーク / 自動（OS 追従）を切り替える。3製品共通（#95）。 */
export function AppearanceSettingsPage({ theme, onThemeChange, messages }: AppearanceSettingsPageProps) {
  const m = { ...APPEARANCE_MESSAGES, ...messages };
  return (
    <>
      <PageHeader wide title={m.title} subtitle={m.subtitle} />
      <PageBody wide className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle>{m.themeLabel}</CardTitle>
            <CardDescription>{m.themeHint}</CardDescription>
          </CardHeader>
          <CardContent>
            <div
              role="group"
              aria-label={m.themeLabel}
              className="flex flex-wrap gap-2"
              data-testid="appearance-theme-toggle"
            >
              {THEME_OPTIONS.map((option) => (
                <Button
                  type="button"
                  variant="secondary"
                  size="md"
                  key={option.value}
                  aria-pressed={theme === option.value}
                  onClick={() => onThemeChange(option.value)}
                >
                  {m[option.label]}
                </Button>
              ))}
            </div>
          </CardContent>
        </Card>
      </PageBody>
    </>
  );
}
