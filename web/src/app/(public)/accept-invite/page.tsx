import { AcceptInviteScreen } from "./screen";

export default async function AcceptInviteRoute({
  searchParams,
}: {
  searchParams: Promise<{ token?: string | string[] }>;
}) {
  const { token } = await searchParams;
  return <AcceptInviteScreen token={typeof token === "string" ? token : ""} />;
}
