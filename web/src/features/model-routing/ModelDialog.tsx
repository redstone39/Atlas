import { Cpu, Server } from "lucide-react";
import { type Dispatch, type SetStateAction } from "react";
import { useTranslation } from "react-i18next";
import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Autocomplete, AutocompleteContent, AutocompleteEmpty, AutocompleteInput, AutocompleteItem, AutocompleteList } from "../../components/ui/autocomplete";
import { Button } from "../../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import { TechnicalDetails, serverMessage } from "../../shared/product-ui";
import type { ModelRouteStatus, ProviderConnectionStatus } from "./types";
import type { RuntimePolicyDraft } from "./runtimePolicy";

export function ModelDialog({
 modelDialogOpen, editingRoute, routeName, modelName, modelConnectionId, modelEnabled,
 modelSupportsVision, runtimePolicy, runtimePolicyTemplateRouteName, availableModels,
 discoveryStatus, discoveryMessage, connections, runtimePolicyInvalid, canSaveModel,
 pendingAction, onClose, onRouteNameChange, onModelNameChange, onModelConnectionIdChange,
 onModelEnabledChange, onModelSupportsVisionChange, onRuntimePolicyChange, onSubmit,
}: {
 modelDialogOpen: boolean; editingRoute: ModelRouteStatus | null; routeName: string;
 modelName: string; modelConnectionId: string; modelEnabled: boolean; modelSupportsVision: boolean;
 runtimePolicy: RuntimePolicyDraft; runtimePolicyTemplateRouteName: string | null;
 availableModels: string[]; discoveryStatus: "idle" | "loading" | "available" | "unavailable";
 discoveryMessage: string; connections: ProviderConnectionStatus[]; runtimePolicyInvalid: boolean;
 canSaveModel: boolean; pendingAction: string; onClose: () => void;
 onRouteNameChange: (value: string) => void; onModelNameChange: (value: string) => void;
 onModelConnectionIdChange: (value: string) => void; onModelEnabledChange: (value: boolean) => void;
 onModelSupportsVisionChange: (value: boolean) => void;
 onRuntimePolicyChange: Dispatch<SetStateAction<RuntimePolicyDraft>>; onSubmit: () => void;
}) {
 const { t } = useTranslation();
 return (
<Dialog open={modelDialogOpen} onOpenChange={(open) => !open && onClose()}>
        <DialogContent className="max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editingRoute ? t("models.editModel") : t("models.addModel")}</DialogTitle>
            <DialogDescription>{t("models.modelDialogDescription")}</DialogDescription>
          </DialogHeader>
          <FieldGroup>
            {editingRoute ? (
              <Field>
                <FieldLabel htmlFor="route-id">{t("admin.routeId")}</FieldLabel>
                <Input id="route-id" value={editingRoute.route_id} readOnly />
                <FieldDescription>{t("models.routeIdLocked")}</FieldDescription>
              </Field>
            ) : null}
            <Field>
              <FieldLabel htmlFor="route-name">{t("admin.routeName")}</FieldLabel>
              <Input id="route-name" value={routeName} onChange={(event) => onRouteNameChange(event.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="model-connection">{t("models.connection")}</FieldLabel>
              <Select value={modelConnectionId} onValueChange={onModelConnectionIdChange}>
                <SelectTrigger id="model-connection" className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {connections.map((connection) => (
                      <SelectItem key={connection.connection_id} value={connection.connection_id}>
                        {connection.display_name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </Field>
            <Field>
              <FieldLabel htmlFor="model-name">{t("models.modelOrDeploymentName")}</FieldLabel>
              <Autocomplete
                items={availableModels}
                value={modelName}
                onValueChange={onModelNameChange}
                openOnInputClick
              >
                <AutocompleteInput
                  id="model-name"
                  className="w-full"
                  placeholder={t("models.modelOrDeploymentName")}
                  triggerLabel={t("models.showModelSuggestions")}
                />
                <AutocompleteContent>
                  <AutocompleteEmpty>{t("models.modelSuggestionsEmpty")}</AutocompleteEmpty>
                  <AutocompleteList>
                    {(model) => (
                      <AutocompleteItem key={model} value={model}>
                        {model}
                      </AutocompleteItem>
                    )}
                  </AutocompleteList>
                </AutocompleteContent>
              </Autocomplete>
              <FieldDescription>
                {discoveryStatus === "loading" ? t("models.discoveryLoading") : t("models.modelNameHelp")}
              </FieldDescription>
            </Field>
            {discoveryStatus === "unavailable" ? (
              <Alert>
                <Server />
                <AlertTitle>{t("models.discoveryUnavailable")}</AlertTitle>
                <AlertDescription>{serverMessage(discoveryMessage, t)} {t("models.discoveryUnavailableDescription")}</AlertDescription>
              </Alert>
            ) : null}
            <Field orientation="horizontal">
              <div>
                <FieldLabel htmlFor="model-enabled">{t("models.modelEnabled")}</FieldLabel>
                <FieldDescription>{t("models.modelEnabledDescription")}</FieldDescription>
              </div>
              <Switch id="model-enabled" checked={modelEnabled} onCheckedChange={onModelEnabledChange} />
            </Field>
            <Field orientation="horizontal">
              <div>
                <FieldLabel htmlFor="model-supports-vision">{t("models.visionEnabled")}</FieldLabel>
                <FieldDescription>{t("models.visionEnabledDescription")}</FieldDescription>
              </div>
              <Switch
                id="model-supports-vision"
                checked={modelSupportsVision}
                onCheckedChange={onModelSupportsVisionChange}
              />
            </Field>
            <TechnicalDetails label={t("common.advancedSettings")}>
              <div className="flex flex-col gap-5 rounded-md border p-4">
            <FieldSet>
              <FieldLegend>{t("models.execution")}</FieldLegend>
              {runtimePolicyTemplateRouteName ? (
                <FieldDescription>
                  {t("models.runtimePolicyTemplate", {
                    route: runtimePolicyTemplateRouteName,
                  })}
                </FieldDescription>
              ) : null}
              <FieldGroup className="grid gap-4 sm:grid-cols-2">
                <Field>
                  <FieldLabel htmlFor="tokenizer_profile">{t("models.tokenizerProfile")}</FieldLabel>
                  <Input
                    id="tokenizer_profile"
                    required
                    value={runtimePolicy.tokenizer_profile}
                    onChange={(event) => onRuntimePolicyChange((current) => ({
                      ...current,
                      tokenizer_profile: event.target.value,
                    }))}
                  />
                </Field>
                {editingRoute ? (
                  <Field>
                    <FieldLabel htmlFor="runtime-policy-revision">{t("models.policyRevisionLabel")}</FieldLabel>
                    <Input
                      id="runtime-policy-revision"
                      value={editingRoute.runtime_policy.revision}
                      readOnly
                    />
                    <FieldDescription>{t("models.policyRevisionReadOnly")}</FieldDescription>
                  </Field>
                ) : null}
                <PolicyNumberField
                  id="max_tool_executions"
                  label={t("models.maxToolExecutions")}
                  value={runtimePolicy.max_tool_executions}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_tool_executions: value }))}
                />
                <PolicyNumberField
                  id="max_provider_invocations"
                  label={t("models.maxProviderInvocations")}
                  value={runtimePolicy.max_provider_invocations}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_provider_invocations: value }))}
                />
                <PolicyNumberField
                  id="max_reasoning_revision_cycles"
                  label={t("models.maxReasoningRevisionCycles")}
                  value={runtimePolicy.max_reasoning_revision_cycles}
                  min={0}
                  max={3}
                  onChange={(value) => onRuntimePolicyChange((current) => ({
                    ...current,
                    max_reasoning_revision_cycles: value,
                  }))}
                />
                <PolicyNumberField
                  id="max_catalog_pages"
                  label={t("models.maxCatalogPages")}
                  value={runtimePolicy.max_catalog_pages}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_catalog_pages: value }))}
                />
                <PolicyNumberField
                  id="max_search_rounds"
                  label={t("models.maxSearchRounds")}
                  value={runtimePolicy.max_search_rounds}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_search_rounds: value }))}
                />
                <PolicyNumberField
                  id="max_model_visible_items_per_turn"
                  label={t("models.maxModelVisibleItemsPerTurn")}
                  value={runtimePolicy.max_model_visible_items_per_turn}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_model_visible_items_per_turn: value }))}
                />
                <PolicyNumberField
                  id="max_retrieval_repairs"
                  label={t("models.maxRetrievalRepairs")}
                  value={runtimePolicy.max_retrieval_repairs}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_retrieval_repairs: value }))}
                />
                <PolicyNumberField
                  id="max_schema_retries_per_turn"
                  label={t("models.maxSchemaRetriesPerTurn")}
                  value={runtimePolicy.max_schema_retries_per_turn}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_schema_retries_per_turn: value }))}
                />
                <PolicyNumberField
                  id="max_selected_anchor_pages_per_round"
                  label={t("models.maxSelectedAnchorPagesPerRound")}
                  value={runtimePolicy.max_selected_anchor_pages_per_round}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_selected_anchor_pages_per_round: value }))}
                />
              </FieldGroup>
            </FieldSet>
            <FieldSet>
              <FieldLegend>{t("models.timeouts")}</FieldLegend>
              <FieldGroup className="grid gap-4 sm:grid-cols-3">
                <PolicyNumberField
                  id="provider_invocation_timeout_seconds"
                  label={t("models.providerTimeout")}
                  value={runtimePolicy.provider_invocation_timeout_seconds}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, provider_invocation_timeout_seconds: value }))}
                />
                <PolicyNumberField
                  id="tool_execution_timeout_seconds"
                  label={t("models.toolTimeout")}
                  value={runtimePolicy.tool_execution_timeout_seconds}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, tool_execution_timeout_seconds: value }))}
                />
                <PolicyNumberField
                  id="turn_timeout_seconds"
                  label={t("models.turnTimeout")}
                  value={runtimePolicy.turn_timeout_seconds}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, turn_timeout_seconds: value }))}
                />
              </FieldGroup>
            </FieldSet>
            <FieldSet data-invalid={runtimePolicyInvalid || undefined}>
              <FieldLegend>{t("models.tokenBudgets")}</FieldLegend>
              <FieldGroup className="grid gap-4 sm:grid-cols-2">
                <PolicyNumberField
                  id="context_window_tokens"
                  label={t("models.contextWindowTokens")}
                  value={runtimePolicy.context_window_tokens}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, context_window_tokens: value }))}
                />
                <PolicyNumberField
                  id="max_input_tokens_per_invocation"
                  label={t("models.maxInputTokens")}
                  value={runtimePolicy.max_input_tokens_per_invocation}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_input_tokens_per_invocation: value }))}
                />
                <PolicyNumberField
                  id="max_output_tokens_per_invocation"
                  label={t("models.maxOutputTokens")}
                  value={runtimePolicy.max_output_tokens_per_invocation}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_output_tokens_per_invocation: value }))}
                />
                <PolicyNumberField
                  id="max_tool_result_tokens_per_execution"
                  label={t("models.maxToolResultTokens")}
                  value={runtimePolicy.max_tool_result_tokens_per_execution}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_tool_result_tokens_per_execution: value }))}
                />
                <PolicyNumberField
                  id="max_total_tokens_per_conversation"
                  label={t("models.maxConversationTokens")}
                  value={runtimePolicy.max_total_tokens_per_conversation}
                  onChange={(value) => onRuntimePolicyChange((current) => ({ ...current, max_total_tokens_per_conversation: value }))}
                />
              </FieldGroup>
              {runtimePolicyInvalid ? <FieldError>{t("models.runtimePolicyInvalid")}</FieldError> : null}
            </FieldSet>
              </div>
            </TechnicalDetails>
          </FieldGroup>
          <DialogFooter>
            <Button variant="outline" onClick={onClose}>{t("admin.cancel")}</Button>
            <Button
              disabled={!canSaveModel || pendingAction === "save-model"}
              onClick={onSubmit}
            >
              <Cpu data-icon="inline-start" />
              {t("models.saveModel")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
 );
}

function PolicyNumberField({
  id, label, value, onChange, min = 1, max,
}: {
  id: keyof RuntimePolicyDraft; label: string; value: string;
  onChange: (value: string) => void; min?: number; max?: number;
}) {
  return (
    <Field>
      <FieldLabel htmlFor={id}>{label}</FieldLabel>
      <Input id={id} type="number" inputMode="numeric" min={min} max={max}
        step={1} required value={value} onChange={(event) => onChange(event.target.value)} />
    </Field>
  );
}
