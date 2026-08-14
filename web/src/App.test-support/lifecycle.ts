import { cleanup } from "@testing-library/react";
import { vi } from "vitest";

import i18n, { LANGUAGE_STORAGE_KEY } from "../i18n";
import { THEME_STORAGE_KEY } from "../shared/theme";

export async function prepareAppTest() {
  window.sessionStorage.clear();
  window.innerWidth = 1024;
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    writable: true,
    value: 1024,
  });
  vi.spyOn(window.HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  if (!window.HTMLElement.prototype.scrollIntoView) {
    Object.defineProperty(window.HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn(),
    });
  }
  if (!URL.createObjectURL) {
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:atlas-test"),
    });
  }
  if (!URL.revokeObjectURL) {
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  }
  if (typeof window.localStorage.setItem === "function") {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "en");
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  }
  document.documentElement.classList.remove("dark");
  document.documentElement.classList.add("light");
  await i18n.changeLanguage("en");
  window.history.pushState({}, "", "/login");
}

export function cleanupAppTest() {
  cleanup();
  window.sessionStorage.clear();
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  document.documentElement.classList.remove("light", "dark");
  vi.useRealTimers();
  vi.restoreAllMocks();
}
