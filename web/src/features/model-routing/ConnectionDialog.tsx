import { CloudCog } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, FieldDescription, FieldGroup, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import type { ProviderConnectionStatus, ProviderType } from "./types";

const providerDefaults: Record<ProviderType, string> = {
  openai_compatible: "https://api.openai.com/v1",
  azure_openai: "https://example.openai.azure.com",
  anthropic: "https://api.anthropic.com",
};

export function ConnectionDialog({
 connectionDialogOpen, editingConnection, connectionName, providerType, endpointUrl,
 apiVersion, apiKey, connectionEnabled, canSaveConnection, pendingAction,
 onClose, onConnectionNameChange, onProviderTypeChange, onEndpointUrlChange,
 onApiVersionChange, onApiKeyChange, onConnectionEnabledChange, onSubmit,
}: {
 connectionDialogOpen: boolean; editingConnection: ProviderConnectionStatus | null;
 connectionName: string; providerType: ProviderType; endpointUrl: string; apiVersion: string;
 apiKey: string; connectionEnabled: boolean; canSaveConnection: boolean; pendingAction: string;
 onClose: () => void; onConnectionNameChange: (value: string) => void;
 onProviderTypeChange: (value: ProviderType) => void; onEndpointUrlChange: (value: string) => void;
 onApiVersionChange: (value: string) => void; onApiKeyChange: (value: string) => void;
 onConnectionEnabledChange: (value: boolean) => void; onSubmit: () => void;
}) {
 const { t } = useTranslation();
 return (
<Dialog open={connectionDialogOpen} onOpenChange={(open) => !open && onClose()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editingConnection ? t("models.editConnection") : t("models.addConnection")}
            </DialogTitle>
            <DialogDescription>{t("models.connectionDialogDescription")}</DialogDescription>
          </DialogHeader>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="connection-name">{t("models.connectionName")}</FieldLabel>
              <Input id="connection-name" value={connectionName} onChange={(event) => onConnectionNameChange(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="provider-type">{t("admin.providerType")}</FieldLabel>
              <Select
                value={providerType}
                disabled={Boolean(editingConnection)}
                onValueChange={(value) => {
                  const nextType = value as ProviderType;
                  onProviderTypeChange(nextType);
                  onEndpointUrlChange(providerDefaults[nextType]);
                }}
              >
                <SelectTrigger id="provider-type" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectItem value="openai_compatible">{t("admin.providerOpenAICompatible")}</SelectItem>
                    <SelectItem value="azure_openai">{t("models.providerAzure")}</SelectItem>
                    <SelectItem value="anthropic">{t("models.providerAnthropic")}</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
              {editingConnection ? <FieldDescription>{t("models.providerTypeLocked")}</FieldDescription> : null}
            </Field>
            <Field>
              <FieldLabel htmlFor="endpoint-url">{t("admin.endpointUrl")}</FieldLabel>
              <Input id="endpoint-url" value={endpointUrl} onChange={(event) => onEndpointUrlChange(event.target.value)} />
            </Field>
            {providerType === "azure_openai" ? (
              <Field>
                <FieldLabel htmlFor="api-version">{t("models.apiVersion")}</FieldLabel>
                <Input
                  id="api-version"
                  required
                  value={apiVersion}
                  onChange={(event) => onApiVersionChange(event.target.value)}
                />
                <FieldDescription>{t("models.apiVersionDescription")}</FieldDescription>
              </Field>
            ) : null}
            <Field>
              <FieldLabel htmlFor="api-key">{t("models.apiKey")}</FieldLabel>
              <Input id="api-key" type="password" autoComplete="new-password" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} />
              <FieldDescription>
                {editingConnection ? t("models.apiKeyBlankPreserves") : t("models.apiKeyNeverShown")}
              </FieldDescription>
            </Field>
            {editingConnection ? (
              <Field orientation="horizontal">
                <div>
                  <FieldLabel htmlFor="connection-enabled">{t("models.connectionEnabled")}</FieldLabel>
                  <FieldDescription>{t("models.connectionEnabledDescription")}</FieldDescription>
                </div>
                <Switch id="connection-enabled" checked={connectionEnabled} onCheckedChange={onConnectionEnabledChange} />
              </Field>
            ) : null}
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>{t("admin.cancel")}</Button>
            <Button
              disabled={!canSaveConnection || pendingAction === "save-connection"}
              onClick={onSubmit}
            >
              <CloudCog data-icon="inline-start" />
              {t("models.saveConnection")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
 );
}
