import { CheckCircle2, KeyRound } from "lucide-react";
import { useState } from "react";
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
import { passwordConfirmationState } from "../identity-session";

export function AcceptInviteFeature({
  token,
  onDone,
}: {
  token: string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [accepted, setAccepted] = useState(false);
  const passwordState = passwordConfirmationState(password, confirmPassword);

  async function acceptInvite() {
    if (!token || !passwordState.valid) return;
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
              <Field data-invalid={passwordState.tooShort}>
                <FieldLabel htmlFor="new-password">{t("invite.password")}</FieldLabel>
                <Input
                  id="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  aria-invalid={passwordState.tooShort}
                  onChange={(event) => {
                    setPassword(event.target.value);
                    setError("");
                  }}
                />
                <FieldDescription>
                  {passwordState.tooShort ? t("invite.passwordTooShort") : t("invite.passwordHelp")}
                </FieldDescription>
              </Field>
              <Field data-invalid={passwordState.mismatch}>
                <FieldLabel htmlFor="confirm-password">{t("invite.confirm")}</FieldLabel>
                <Input
                  id="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  aria-invalid={passwordState.mismatch}
                  onChange={(event) => {
                    setConfirmPassword(event.target.value);
                    setError("");
                  }}
                />
                {passwordState.mismatch && (
                  <FieldDescription>{t("invite.passwordMismatch")}</FieldDescription>
                )}
              </Field>
              {error && (
                <Alert variant="destructive">
                  <AlertTitle>{t("invite.acceptFailed")}</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}
              <Button onClick={acceptInvite} disabled={!token || !passwordState.valid || pending}>
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
