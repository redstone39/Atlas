import {
  LoginFeature,
  type SessionState,
} from "../features/identity-session/index";

export function LoginPage({
  session,
  authUnavailable = false,
  onLogin,
}: {
  session: SessionState;
  authUnavailable?: boolean;
  onLogin: (session: SessionState) => void;
}) {
  return (
    <LoginFeature
      session={session}
      authUnavailable={authUnavailable}
      onLogin={onLogin}
    />
  );
}
