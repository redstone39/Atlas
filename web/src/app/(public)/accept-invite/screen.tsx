"use client";

import { useRouter } from "next/navigation";

import { AcceptInvitePage } from "@/components/pages/AcceptInvitePage";

export function AcceptInviteScreen({ token }: { token: string }) {
  const router = useRouter();
  return <AcceptInvitePage token={token} onDone={() => router.push("/login")} />;
}
