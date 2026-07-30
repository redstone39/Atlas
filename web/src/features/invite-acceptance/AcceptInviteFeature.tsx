import { CheckCircle2, KeyRound } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import { serverMessage } from "../../shared/product-ui";
import { inviteAcceptanceApi } from "./api";

export function AcceptInviteFeature({ onDone }: { onDone: () => void }) {
  const { t } = useTranslation();
  const token = useMemo(
    () => new URLSearchParams(window.location.search).get("token") ?? "",
    [],
  );
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState(false);
  const passwordTooShort = password.length > 0 && password.length < 12;
  const passwordMismatch =
    password.length >= 12 && confirmPassword.length > 0 && password !== confirmPassword;
  const passwordsMatch = password.length >= 12 && password === confirmPassword;

  async function acceptInvite() {
    if (!token || !passwordsMatch) return;
    setPending(true);
    setError("");
    try {
      const result = await inviteAcceptanceApi.acceptInvite(token, password);
      setAccepted(true);
      toast.success(serverMessage(result, t));
    } catch (err) {
      setError(serverMessage(err, t));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-background px-4 py-10">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>
            <h1>{t("invite.title")}</h1>
          </CardTitle>
          <CardDescription>{t("invite.description")}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {!token && (
            <Alert variant="destructive">
              <AlertTitle>{t("invite.missingTitle")}</AlertTitle>
              <AlertDescription>{t("invite.missingDescription")}</AlertDescription>
            </Alert>
          )}
          {accepted ? (
            <Alert>
              <CheckCircle2 />
              <AlertTitle>{t("invite.acceptedTitle")}</AlertTitle>
              <AlertDescription>{t("invite.acceptedDescription")}</AlertDescription>
            </Alert>
          ) : (
            <FieldGroup>
              <Field data-invalid={passwordTooShort}>
                <FieldLabel htmlFor="new-password">{t("invite.password")}</FieldLabel>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  aria-invalid={passwordTooShort}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError("");
                  }}
                />
                <FieldDescription>
                  {passwordTooShort ? t("invite.passwordTooShort") : t("invite.passwordHelp")}
                </FieldDescription>
              </Field>
              <Field data-invalid={passwordMismatch}>
                <FieldLabel htmlFor="confirm-password">{t("invite.confirm")}</FieldLabel>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  aria-invalid={passwordMismatch}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                    setError("");
                  }}
                />
                {passwordMismatch && (
                  <FieldDescription>{t("invite.passwordMismatch")}</FieldDescription>
                )}
              </Field>
              {error && (
                <Alert variant="destructive">
                  <AlertTitle>{t("invite.acceptFailed")}</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button onClick={acceptInvite} disabled={!token || !passwordsMatch || pending}>
                {pending ? <Spinner data-icon="inline-start" /> : <KeyRound data-icon="inline-start" />}
                {t("invite.accept")}
              </Button>
            </FieldGroup>
          )}
          <Button variant="outline" onClick={onDone}>
            {accepted ? t("invite.goLogin") : t("login.submit")}
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
