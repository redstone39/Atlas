import {
  LoginFeature,
  type SessionState,
} from "../../features/identity-session/index";

export function LoginPage({
  session,
  authUnavailable = false,
  firstAdminStatusUnavailable = false,
  firstAdminClaimed = false,
  loginAllowed = true,
  onRetryFirstAdminStatus,
  onLogin,
}: {
  session: SessionState;
  authUnavailable?: boolean;
  firstAdminStatusUnavailable?: boolean;
  firstAdminClaimed?: boolean;
  loginAllowed?: boolean;
  onRetryFirstAdminStatus?: () => void;
  onLogin: (session: SessionState) => void;
}) {
  return (
    <LoginFeature
      session={session}
      authUnavailable={authUnavailable}
      firstAdminStatusUnavailable={firstAdminStatusUnavailable}
      firstAdminClaimed={firstAdminClaimed}
      loginAllowed={loginAllowed}
      onRetryFirstAdminStatus={onRetryFirstAdminStatus}
      onLogin={onLogin}
    />
  );
}
