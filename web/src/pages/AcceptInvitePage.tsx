import { AcceptInviteFeature } from "../features/invite-acceptance/index";

export function AcceptInvitePage({ onDone }: { onDone: () => void }) {
  return <AcceptInviteFeature onDone={onDone} />;
}
