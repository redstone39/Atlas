"use client";

import { usePathname } from "next/navigation";

import { AdminResourceUnavailable } from "@/shared/admin-detail";
import type { AppRoute } from "@/shared/routes";
import { useAuthenticatedShell } from "../layout";

export default function UnavailableRoute() {
  const pathname = usePathname();
  const { navigate } = useAuthenticatedShell();
  let backRoute: AppRoute = "/settings";
  if (pathname.startsWith("/admin/users/")) backRoute = "/admin/users";
  else if (pathname.startsWith("/admin/teams/")) backRoute = "/admin/teams";
  else if (pathname.startsWith("/admin/projects/")) backRoute = "/admin/projects";
  else if (pathname.startsWith("/admin/audit/")) backRoute = "/admin/audit";
  return <AdminResourceUnavailable onBack={() => navigate(backRoute)} />;
}
