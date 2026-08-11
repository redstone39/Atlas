"use client";

import type { AppRoute } from "@/shared/routes";
import { TeamsScreen } from "../../screen";

export function TeamMembersScreen({ route }: { route: AppRoute }) {
  return <TeamsScreen route={route} />;
}
