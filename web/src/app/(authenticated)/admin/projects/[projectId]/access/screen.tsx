"use client";

import type { AppRoute } from "@/shared/routes";
import { ProjectsScreen } from "../../screen";

export function ProjectAccessScreen({ route }: { route: AppRoute }) {
  return <ProjectsScreen route={route} />;
}
