"use client";

import type { AppRoute } from "@/shared/routes";
import { UsersScreen } from "../screen";

export function UserDetailScreen({ route }: { route: AppRoute }) {
  return <UsersScreen route={route} />;
}
