import { Moon, Sun } from "lucide-react";
import { ThemeProvider, useTheme } from "next-themes";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Field, FieldLabel } from "../components/ui/field";
import { ToggleGroup, ToggleGroupItem } from "../components/ui/toggle-group";

export const THEME_STORAGE_KEY = "atlas.production.theme";

export function AtlasThemeProvider({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem={false}
      enableColorScheme
      disableTransitionOnChange
      storageKey={THEME_STORAGE_KEY}
    >
      {children}
    </ThemeProvider>
  );
}

export function ThemeSwitch() {
  const { t } = useTranslation();
  const { theme, setTheme } = useTheme();
  const activeTheme = theme === "dark" ? "dark" : "light";

  function changeTheme(next: string) {
    if (next === "light" || next === "dark") {
      setTheme(next);
    }
  }

  return (
    <Field orientation="horizontal" className="w-auto">
      <FieldLabel className="sr-only">{t("theme.label")}</FieldLabel>
      <ToggleGroup
        type="single"
        value={activeTheme}
        onValueChange={changeTheme}
        variant="outline"
        size="sm"
        aria-label={t("theme.label")}
      >
        <ToggleGroupItem value="light" aria-label={t("theme.lightLabel")}>
          <Sun data-icon="inline-start" />
          {t("theme.light")}
        </ToggleGroupItem>
        <ToggleGroupItem value="dark" aria-label={t("theme.darkLabel")}>
          <Moon data-icon="inline-start" />
          {t("theme.dark")}
        </ToggleGroupItem>
      </ToggleGroup>
    </Field>
  );
}
