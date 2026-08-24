import { BrainCircuit } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
} from "../../components/ui/field";
import { Spinner } from "../../components/ui/spinner";
import { Switch } from "../../components/ui/switch";
import { ApiError } from "../../shared/user-messages";
import { conversationReviewApi } from "./api";
import type { ConversationLearningSettings } from "./types";

export function ConversationLearningSettingsFeature() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<ConversationLearningSettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [saveError, setSaveError] = useState(false);
  const [stale, setStale] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(false);
    conversationReviewApi.getLearningSettings().then((loaded) => {
      if (!active) return;
      setSettings(loaded);
      setEnabled(loaded.enabled);
      setLoading(false);
    }).catch(() => {
      if (!active) return;
      setSettings(null);
      setLoading(false);
      setLoadError(true);
    });
    return () => { active = false; };
  }, [reloadKey]);

  async function save() {
    if (!settings || enabled === settings.enabled) return;
    const proposedEnabled = enabled;
    setSaving(true);
    setSaveError(false);
    setStale(false);
    try {
      const updated = await conversationReviewApi.updateLearningSettings(
        settings,
        proposedEnabled,
      );
      setSettings(updated);
      setEnabled(updated.enabled);
      toast.success(t("conversationLearning.settingsSaved"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        try {
          const current = await conversationReviewApi.getLearningSettings();
          setSettings(current);
          setEnabled(proposedEnabled);
          setStale(true);
        } catch {
          setSettings(null);
          setLoadError(true);
        }
      } else {
        setSaveError(true);
        toast.error(t("conversationLearning.settingsSaveFailed"));
      }
    } finally {
      setSaving(false);
    }
  }

  const dirty = settings !== null && enabled !== settings.enabled;

  return (
    <Card>
      <CardHeader>
        <CardTitle role="heading" aria-level={2} className="flex items-center gap-2">
          <BrainCircuit data-icon="inline-start" />
          {t("conversationLearning.settingsTitle")}
        </CardTitle>
        <CardDescription>{t("conversationLearning.settingsDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {stale && (
          <Alert>
            <AlertTitle>{t("conversationLearning.settingsStaleTitle")}</AlertTitle>
            <AlertDescription>{t("conversationLearning.settingsStaleDescription")}</AlertDescription>
          </Alert>
        )}
        {saveError && (
          <Alert variant="destructive">
            <AlertTitle>{t("conversationLearning.settingsSaveFailed")}</AlertTitle>
            <AlertDescription>{t("conversationLearning.settingsSaveFailedDescription")}</AlertDescription>
          </Alert>
        )}
        {loadError ? (
          <Alert variant="destructive">
            <AlertTitle>{t("conversationLearning.settingsLoadFailed")}</AlertTitle>
            <AlertDescription className="flex flex-col items-start gap-3">
              <span>{t("conversationLearning.settingsLoadFailedDescription")}</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReloadKey((current) => current + 1)}
              >
                {t("admin.retry")}
              </Button>
            </AlertDescription>
          </Alert>
        ) : loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
            <Spinner />
            {t("conversationLearning.settingsLoading")}
          </div>
        ) : settings ? (
          <>
            <Field orientation="horizontal">
              <FieldContent>
                <FieldLabel htmlFor="conversation-learning-enabled">
                  {t("conversationLearning.enabledLabel")}
                </FieldLabel>
                <FieldDescription>
                  {t("conversationLearning.enabledDescription")}
                </FieldDescription>
              </FieldContent>
              <Switch
                id="conversation-learning-enabled"
                checked={enabled}
                disabled={saving}
                onCheckedChange={(checked) => {
                  setEnabled(checked);
                  setSaveError(false);
                }}
              />
            </Field>
            <dl className="grid gap-2 rounded-md border bg-muted/30 p-3 text-sm sm:grid-cols-3">
              <div>
                <dt className="text-xs text-muted-foreground">{t("conversationLearning.currentState")}</dt>
                <dd className="font-medium">
                  {t(settings.enabled ? "conversationLearning.stateEnabled" : "conversationLearning.stateDisabled")}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("conversationLearning.revision")}</dt>
                <dd className="font-medium">{settings.settings_revision}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{t("conversationLearning.lastUpdated")}</dt>
                <dd className="font-medium">
                  <time dateTime={settings.updated_at}>{settings.updated_at}</time>
                  <span className="block text-xs font-normal text-muted-foreground">
                    {settings.updated_actor_id}
                  </span>
                </dd>
              </div>
            </dl>
          </>
        ) : null}
      </CardContent>
      {!loadError && (
        <CardFooter>
          <Button
            disabled={loading || saving || !dirty}
            onClick={() => void save()}
          >
            {saving && <Spinner data-icon="inline-start" />}
            {t("conversationLearning.saveSettings")}
          </Button>
        </CardFooter>
      )}
    </Card>
  );
}
