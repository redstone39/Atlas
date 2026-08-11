"use client";

import { useEffect, type ReactNode } from "react";

import "@/i18n";
import { initializeBrowserLanguage } from "@/i18n";
import { Toaster } from "@/components/ui/sonner";
import { AtlasThemeProvider } from "@/shared/theme";
import { AtlasSessionProvider } from "./session-provider";

export function Providers({ children }: { children: ReactNode }) {
  useEffect(() => {
    void initializeBrowserLanguage();
  }, []);

  return (
    <AtlasThemeProvider>
      <AtlasSessionProvider>{children}</AtlasSessionProvider>
      <Toaster richColors position="top-right" />
    </AtlasThemeProvider>
  );
}
