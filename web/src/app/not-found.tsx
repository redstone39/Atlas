"use client";

import { useRouter } from "next/navigation";

import { AdminResourceUnavailable } from "@/shared/admin-detail";

export default function NotFoundPage() {
  const router = useRouter();
  return <AdminResourceUnavailable onBack={() => router.push("/settings")} />;
}
