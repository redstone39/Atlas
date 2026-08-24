import { CloudCog } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, FieldDescription, FieldLabel } from "../../components/ui/field";
import { Switch } from "../../components/ui/switch";
import type { ProviderConnectionStatus, ProviderType } from "./types";
import { ProviderConnectionFields } from "./provider-connection-fields";


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
          <ProviderConnectionFields
            idPrefix="models"
            values={{
              displayName: connectionName,
              providerType,
              endpointUrl,
              apiVersion,
              apiKey,
            }}
            providerTypeLocked={Boolean(editingConnection)}
            credentialAlreadyConfigured={Boolean(editingConnection?.credential_configured)}
            onDisplayNameChange={onConnectionNameChange}
            onProviderTypeChange={onProviderTypeChange}
            onEndpointUrlChange={onEndpointUrlChange}
            onApiVersionChange={onApiVersionChange}
            onApiKeyChange={onApiKeyChange}
          />
          {editingConnection ? (
            <Field orientation="horizontal">
              <div>
                <FieldLabel htmlFor="connection-enabled">{t("models.connectionEnabled")}</FieldLabel>
                <FieldDescription>{t("models.connectionEnabledDescription")}</FieldDescription>
              </div>
              <Switch id="connection-enabled" checked={connectionEnabled} onCheckedChange={onConnectionEnabledChange} />
            </Field>
          ) : null}
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
