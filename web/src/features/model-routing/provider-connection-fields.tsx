import { useTranslation } from "react-i18next";

import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import type { ProviderType } from "./types";

export const providerEndpointDefaults: Record<ProviderType, string> = {
  openai_compatible: "https://api.openai.com/v1",
  azure_openai: "https://example.openai.azure.com",
  anthropic: "https://api.anthropic.com",
};

export interface ProviderConnectionFieldValues {
  displayName: string;
  providerType: ProviderType;
  endpointUrl: string;
  apiVersion: string;
  apiKey: string;
}

export function providerConnectionFieldsValid(
  fields: ProviderConnectionFieldValues,
  credentialAlreadyConfigured = false,
): boolean {
  return Boolean(
    fields.displayName.trim() &&
      fields.endpointUrl.trim() &&
      (fields.providerType !== "azure_openai" || fields.apiVersion.trim()) &&
      (credentialAlreadyConfigured || fields.apiKey.trim()),
  );
}

export function ProviderConnectionFields({
  idPrefix,
  values,
  providerTypeLocked = false,
  credentialAlreadyConfigured = false,
  onDisplayNameChange,
  onProviderTypeChange,
  onEndpointUrlChange,
  onApiVersionChange,
  onApiKeyChange,
}: {
  idPrefix: string;
  values: ProviderConnectionFieldValues;
  providerTypeLocked?: boolean;
  credentialAlreadyConfigured?: boolean;
  onDisplayNameChange: (value: string) => void;
  onProviderTypeChange: (value: ProviderType) => void;
  onEndpointUrlChange: (value: string) => void;
  onApiVersionChange: (value: string) => void;
  onApiKeyChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const connectionNameId = `${idPrefix}-connection-name`;
  const providerTypeId = `${idPrefix}-provider-type`;
  const endpointUrlId = `${idPrefix}-endpoint-url`;
  const apiVersionId = `${idPrefix}-api-version`;
  const apiKeyId = `${idPrefix}-api-key`;

  return (
    <FieldGroup>
      <Field>
        <FieldLabel htmlFor={connectionNameId}>{t("models.connectionName")}</FieldLabel>
        <Input
          id={connectionNameId}
          value={values.displayName}
          onChange={(event) => onDisplayNameChange(event.target.value)}
          required
        />
      </Field>
      <Field>
        <FieldLabel htmlFor={providerTypeId}>{t("admin.providerType")}</FieldLabel>
        <Select
          value={values.providerType}
          disabled={providerTypeLocked}
          onValueChange={(value) => {
            const nextType = value as ProviderType;
            onProviderTypeChange(nextType);
            onEndpointUrlChange(providerEndpointDefaults[nextType]);
          }}
        >
          <SelectTrigger id={providerTypeId} className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="openai_compatible">
                {t("admin.providerOpenAICompatible")}
              </SelectItem>
              <SelectItem value="azure_openai">{t("models.providerAzure")}</SelectItem>
              <SelectItem value="anthropic">{t("models.providerAnthropic")}</SelectItem>
            </SelectGroup>
          </SelectContent>
        </Select>
        {providerTypeLocked && (
          <FieldDescription>{t("models.providerTypeLocked")}</FieldDescription>
        )}
      </Field>
      <Field>
        <FieldLabel htmlFor={endpointUrlId}>{t("admin.endpointUrl")}</FieldLabel>
        <Input
          id={endpointUrlId}
          type="url"
          value={values.endpointUrl}
          onChange={(event) => onEndpointUrlChange(event.target.value)}
          required
        />
      </Field>
      {values.providerType === "azure_openai" && (
        <Field>
          <FieldLabel htmlFor={apiVersionId}>{t("models.apiVersion")}</FieldLabel>
          <Input
            id={apiVersionId}
            value={values.apiVersion}
            onChange={(event) => onApiVersionChange(event.target.value)}
            required
          />
          <FieldDescription>{t("models.apiVersionDescription")}</FieldDescription>
        </Field>
      )}
      <Field>
        <FieldLabel htmlFor={apiKeyId}>{t("models.apiKey")}</FieldLabel>
        <Input
          id={apiKeyId}
          type="password"
          autoComplete="new-password"
          value={values.apiKey}
          onChange={(event) => onApiKeyChange(event.target.value)}
          required={!credentialAlreadyConfigured}
        />
        <FieldDescription>
          {credentialAlreadyConfigured
            ? t("models.apiKeyBlankPreserves")
            : t("models.apiKeyNeverShown")}
        </FieldDescription>
      </Field>
    </FieldGroup>
  );
}
