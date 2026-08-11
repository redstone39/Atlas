import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import { en } from "./locales/en";
import { zhTW } from "./locales/zh-TW";

export const LANGUAGE_STORAGE_KEY = "atlas.production.language";
export const supportedLanguages = ["en", "zh-TW"] as const;
export type SupportedLanguage = (typeof supportedLanguages)[number];

const resources = {
  en: { translation: en },
  "zh-TW": { translation: zhTW },
} as const;

function normalizeLanguage(language: string | undefined): SupportedLanguage {
  if (language?.toLowerCase().startsWith("zh")) {
    return "zh-TW";
  }
  return "en";
}

export async function initializeBrowserLanguage(): Promise<void> {
  const storage = window.localStorage;
  const stored =
    storage && typeof storage.getItem === "function"
      ? storage.getItem(LANGUAGE_STORAGE_KEY)
      : null;
  const language =
    stored === "en" || stored === "zh-TW"
      ? stored
      : normalizeLanguage(window.navigator.language);
  await i18n.changeLanguage(language);
}

void i18n.use(initReactI18next).init({
  resources,
  lng: "en",
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
  react: {
    useSuspense: false,
  },
});

export function persistLanguage(language: SupportedLanguage) {
  if (
    typeof window !== "undefined" &&
    window.localStorage &&
    typeof window.localStorage.setItem === "function"
  ) {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language);
  }
}

export default i18n;
