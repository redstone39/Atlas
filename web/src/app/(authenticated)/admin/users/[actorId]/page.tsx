import { notFound } from "next/navigation";

import { adminUserDetailRoute } from "@/shared/routes";
import { UserDetailScreen } from "./screen";

export default async function UserDetailRoute({
  params,
}: {
  params: Promise<{ actorId?: string }>;
}) {
  const { actorId } = await params;
  if (!actorId) notFound();
  return <UserDetailScreen route={adminUserDetailRoute(actorId)} />;
}
