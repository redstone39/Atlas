"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { matchAppRoute, type AppRoute } from "@/shared/routes";
import { WorkspaceScreen } from "./screen";

export default function WorkspaceLayout({ children: _children }: { children: ReactNode }) {
  const pathname = usePathname();
  const match = matchAppRoute(pathname as AppRoute);
  const conversationId =
    match.kind === "workspace-conversation" ? match.conversationId : null;
  return <WorkspaceScreen conversationId={conversationId} />;
}
