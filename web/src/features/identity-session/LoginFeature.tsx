import { CheckCircle2, LogIn } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Field,
  FieldError,
  FieldGroup,
  FieldLabel,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import { LanguageSwitch, serverMessage } from "../../shared/product-ui";
import { identitySessionApi } from "./api";
import type { SessionState } from "./types";

export function LoginFeature({
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
  const { t } = useTranslation();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const canSubmit = Boolean(identifier.trim() && password);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError("");
    try {
      const nextSession = await identitySessionApi.login(identifier.trim(), password);
      toast.success(t("toast.signedIn"));
      onLogin(nextSession);
    } catch (err) {
      const message = err instanceof Error ? err.message : t("login.failure");
      setError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPassword("");
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto grid min-h-screen w-full max-w-6xl gap-8 px-4 py-8 md:grid-cols-[1fr_420px] md:items-center">
        <section className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <Badge variant="outline">{t("login.badge")}</Badge>
            <LanguageSwitch />
          </div>
          <div className="flex flex-col gap-2">
            <h1 className="text-3xl font-semibold tracking-normal md:text-4xl">
              {t("login.title")}
            </h1>
            <p className="max-w-xl text-base text-muted-foreground">
              {t("login.description")}
            </p>
          </div>
          {session.authenticated && (
            <Alert>
              <CheckCircle2 />
              <AlertTitle>{t("login.activeTitle")}</AlertTitle>
              <AlertDescription>
                {t("login.activeDescription", {
                  name: session.actor?.display_name ?? t("app.unknownRole"),
                })}
              </AlertDescription>
            </Alert>
          )}
          {authUnavailable && (
            <Alert variant="destructive">
              <AlertTitle>{t("login.sessionUnavailableTitle")}</AlertTitle>
              <AlertDescription>{t("login.sessionUnavailableDescription")}</AlertDescription>
            </Alert>
          )}
          {firstAdminStatusUnavailable && (
            <Alert variant="destructive">
              <AlertTitle>{t("login.setupStatusUnavailableTitle")}</AlertTitle>
              <AlertDescription className="flex flex-col items-start gap-3">
                <span>{t("login.setupStatusUnavailableDescription")}</span>
                {onRetryFirstAdminStatus && (
                  <Button type="button" variant="outline" onClick={onRetryFirstAdminStatus}>
                    {t("common.retry")}
                  </Button>
                )}
              </AlertDescription>
            </Alert>
          )}
          {firstAdminClaimed && (
            <Alert>
              <CheckCircle2 />
              <AlertTitle>{t("login.setupClaimedTitle")}</AlertTitle>
              <AlertDescription>{t("login.setupClaimedDescription")}</AlertDescription>
            </Alert>
          )}
        </section>
        {loginAllowed && (
          <Card>
            <CardHeader>
              <CardTitle>{t("login.cardTitle")}</CardTitle>
              <CardDescription>{t("login.cardDescription")}</CardDescription>
            </CardHeader>
            <CardContent>
              <form className="flex flex-col gap-5" onSubmit={submit}>
                <FieldGroup>
                  <Field data-invalid={Boolean(error)}>
                    <FieldLabel htmlFor="identifier">{t("login.identifier")}</FieldLabel>
                    <Input
                      id="identifier"
                      name="identifier"
                      type="text"
                      value={identifier}
                      onChange={(event) => setIdentifier(event.target.value)}
                      required
                      autoComplete="username"
                      aria-invalid={Boolean(error)}
                    />
                  </Field>
                  <Field data-invalid={Boolean(error)}>
                    <FieldLabel htmlFor="password">{t("login.password")}</FieldLabel>
                    <Input
                      id="password"
                      name="password"
                      type="password"
                      value={password}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      autoComplete="current-password"
                      aria-invalid={Boolean(error)}
                    />
                    {error && <FieldError>{serverMessage(error, t)}</FieldError>}
                  </Field>
                </FieldGroup>
                <Button type="submit" className="w-full" disabled={submitting || !canSubmit}>
                  {submitting ? (
                    <Spinner data-icon="inline-start" />
                  ) : (
                    <LogIn data-icon="inline-start" />
                  )}
                  {t("login.submit")}
                </Button>
              </form>
            </CardContent>
          </Card>
        )}
      </div>
    </main>
  );
}
