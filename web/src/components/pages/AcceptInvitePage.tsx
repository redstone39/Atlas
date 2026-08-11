import { AcceptInviteFeature } from "../../features/invite-acceptance/index";

export function AcceptInvitePage({
  token,
  onDone,
}: {
  token: string;
  onDone: () => void;
}) {
  return <AcceptInviteFeature token={token} onDone={onDone} />;
}
