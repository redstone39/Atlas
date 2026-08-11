"use client";

import type { AppRoute } from "@/shared/routes";
import { AuditScreen } from "../screen";

export function AuditEventsScreen({ route }: { route: AppRoute }) {
  return <AuditScreen route={route} />;
}
