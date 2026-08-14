import { TimerReset } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../../components/ui/card";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Spinner } from "../../components/ui/spinner";
import { ApiError } from "../../shared/user-messages";
import { notesApi } from "./api";
import type { NotesSettings } from "./types";

export function NotesSettingsFeature() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState<NotesSettings | null>(null);
  const [value, setValue] = useState("30");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [stale, setStale] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setLoadError(false);
    notesApi.getSettings().then((loaded) => {
      if (!active) return;
      setSettings(loaded);
      setValue(String(loaded.checkpoint_interval_seconds));
      setLoading(false);
    }).catch(() => {
      if (!active) return;
      setLoading(false);
      setLoadError(true);
    });
    return () => { active = false; };
  }, [reloadKey]);

  const parsedValue = Number(value);
  const valid = Number.isInteger(parsedValue) && parsedValue > 0;

  async function save() {
    if (!settings || !valid) return;
    setSaving(true);
    setStale(false);
    try {
      const updated = await notesApi.updateSettings(settings, parsedValue);
      setSettings(updated);
      setValue(String(updated.checkpoint_interval_seconds));
      toast.success(t("notes.settingsSaved"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        try {
          const current = await notesApi.getSettings();
          setSettings(current);
          setStale(true);
        } catch {
          setLoadError(true);
        }
      } else {
        toast.error(t("notes.settingsSaveFailed"));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle role="heading" aria-level={2} className="flex items-center gap-2"><TimerReset data-icon="inline-start" />{t("notes.settingsTitle")}</CardTitle>
        <CardDescription>{t("notes.settingsDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {stale && <Alert><AlertTitle>{t("notes.settingsStaleTitle")}</AlertTitle><AlertDescription>{t("notes.settingsStaleDescription")}</AlertDescription></Alert>}
        {loadError ? <Alert variant="destructive"><AlertTitle>{t("notes.settingsLoadFailed")}</AlertTitle><AlertDescription><Button variant="outline" size="sm" onClick={() => setReloadKey((current) => current + 1)}>{t("admin.retry")}</Button></AlertDescription></Alert> : (
          <FieldGroup>
            <Field data-invalid={!valid && !loading}>
              <FieldLabel htmlFor="notes-checkpoint-interval">{t("notes.checkpointInterval")}</FieldLabel>
              <Input id="notes-checkpoint-interval" type="number" min={1} step={1} value={value} disabled={loading || saving} aria-invalid={!valid} onChange={(event) => setValue(event.target.value)} />
              <FieldDescription>{t("notes.checkpointIntervalDescription")}</FieldDescription>
            </Field>
          </FieldGroup>
        )}
      </CardContent>
      {!loadError && <CardFooter><Button disabled={loading || saving || !settings || !valid || parsedValue === settings.checkpoint_interval_seconds} onClick={() => void save()}>{(loading || saving) && <Spinner data-icon="inline-start" />}{t("notes.saveSettings")}</Button></CardFooter>}
    </Card>
  );
}
