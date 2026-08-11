"use client";

import { matchAppRoute, type AppRoute } from "@/shared/routes";

export function ConversationScreen({ route }: { route: AppRoute }) {
  matchAppRoute(route);
  return null;
}
