import {
  Pencil,
  Plus,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "../../components/ui/empty";
import {
  Field,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Switch } from "../../components/ui/switch";
import { Textarea } from "../../components/ui/textarea";
import { generatedId } from "../../shared/ids";
import { LoadErrorState, LoadingState, PageHeader, serverMessage } from "../../shared/product-ui";
import { directoryAdministrationApi } from "./api";
import type {
  DirectoryConnectionConfig,
  DirectoryConnectionStatus,
  DirectoryProviderType,
} from "./types";

type Draft = Omit<DirectoryConnectionConfig, "connection_id">;

const providerDefaults: Record<DirectoryProviderType, Draft> = {
  active_directory: {
    display_name: "",
    priority: 0,
    provider_type: "active_directory",
    host: "",
    port: 636,
    tls_mode: "ldaps",
    connect_timeout_seconds: 10,
    operation_timeout_seconds: 10,
    bind_dn: "",
    user_base_dn: "",
    user_object_filter: "(&(objectCategory=person)(objectClass=user))",
    login_attribute: "userPrincipalName",
    stable_id_attribute: "objectGUID",
    display_name_attribute: "displayName",
    email_attribute: "mail",
    groups_attribute: "memberOf",
    department_attribute: "department",
    title_attribute: "title",
    employee_id_attribute: "employeeID",
    enabled: true,
  },
  ldap: {
    display_name: "",
    priority: 0,
    provider_type: "ldap",
    host: "",
    port: 636,
    tls_mode: "ldaps",
    connect_timeout_seconds: 10,
    operation_timeout_seconds: 10,
    bind_dn: "",
    user_base_dn: "",
    user_object_filter: "(objectClass=person)",
    login_attribute: "uid",
    stable_id_attribute: "entryUUID",
    display_name_attribute: "cn",
    email_attribute: "mail",
    groups_attribute: "memberOf",
    department_attribute: "department",
    title_attribute: "title",
    employee_id_attribute: "employeeNumber",
    enabled: true,
  },
};

export function DirectoryAdministrationFeature({
  onNotice,
  onRefresh,
}: {
  onNotice: (message: string) => void;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [connections, setConnections] = useState<DirectoryConnectionStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [pendingAction, setPendingAction] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<DirectoryConnectionStatus | null>(null);
  const [draft, setDraft] = useState<Draft>({ ...providerDefaults.active_directory });
  const [bindPassword, setBindPassword] = useState("");
  const [customCaPem, setCustomCaPem] = useState("");
  const [clearBindPassword, setClearBindPassword] = useState(false);
  const [clearCustomCa, setClearCustomCa] = useState(false);

  useEffect(() => {
    void refreshConnections();
  }, []);


  async function refreshConnections(showLoading = true) {
    if (showLoading) setLoading(true);
    setLoadError("");
    try {
      const result = await directoryAdministrationApi.listConnections();
      setConnections(result.connections);
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : t("admin.listLoadFailed"));
    } finally {
      if (showLoading) setLoading(false);
    }
  }

  async function runAction(
    actionName: string,
    action: () => Promise<unknown>,
    onSuccess?: () => void,
  ) {
    setPendingAction(actionName);
    setActionError("");
    try {
      const result = await action();
      if (
        result &&
        typeof result === "object" &&
        "message_code" in result &&
        typeof result.message_code === "string"
      ) {
        onNotice(result.message_code);
        const message = serverMessage(result, t);
        if (
          "validation_status" in result &&
          result.validation_status === "failed"
        ) {
          toast.error(message);
        } else {
          toast.success(message);
        }
      }
      onSuccess?.();
      await refreshConnections(false);
      await onRefresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : t("admin.actionFailed");
      setActionError(message);
      toast.error(serverMessage(message, t));
    } finally {
      setPendingAction("");
    }
  }

  function changeProviderType(providerType: DirectoryProviderType) {
    const defaults = providerDefaults[providerType];
    setDraft({
      ...draft,
      provider_type: providerType,
      user_object_filter: defaults.user_object_filter,
      login_attribute: defaults.login_attribute,
      stable_id_attribute: defaults.stable_id_attribute,
      display_name_attribute: defaults.display_name_attribute,
      email_attribute: defaults.email_attribute,
      groups_attribute: defaults.groups_attribute,
      department_attribute: defaults.department_attribute,
      title_attribute: defaults.title_attribute,
      employee_id_attribute: defaults.employee_id_attribute,
    });
  }

  function openCreate() {
    setEditing(null);
    setDraft({ ...providerDefaults.active_directory });
    resetSecrets();
    setDialogOpen(true);
  }

  function openEdit(connection: DirectoryConnectionStatus) {
    const { connection_id: _connectionId, bind_password_configured: _password, custom_ca_configured: _ca, custom_ca_sha256: _digest, ...config } = connection;
    setEditing(connection);
    setDraft(config);
    resetSecrets();
    setDialogOpen(true);
  }

  function resetSecrets() {
    setBindPassword("");
    setCustomCaPem("");
    setClearBindPassword(false);
    setClearCustomCa(false);
  }

  function closeDialog() {
    resetSecrets();
    setEditing(null);
    setDialogOpen(false);
  }

  function updateDraft<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function changeTlsMode(tlsMode: Draft["tls_mode"]) {
    updateDraft("tls_mode", tlsMode);
    if (tlsMode === "plain") {
      setCustomCaPem("");
      setClearCustomCa(false);
    }
  }

  const canSave = Boolean(
    draft.display_name.trim() &&
      draft.host.trim() &&
      draft.bind_dn.trim() &&
      draft.user_base_dn.trim() &&
      draft.user_object_filter.trim() &&
      draft.login_attribute.trim() &&
      draft.stable_id_attribute.trim() &&
      draft.display_name_attribute.trim() &&
      draft.email_attribute.trim() &&
      draft.groups_attribute.trim() &&
      draft.department_attribute.trim() &&
      draft.title_attribute.trim() &&
      draft.employee_id_attribute.trim() &&
      (editing || bindPassword),
  );


  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t("directory.title")} description={t("directory.description")} />
        <Button onClick={openCreate}>
          <Plus data-icon="inline-start" />
          {t("directory.addConnection")}
        </Button>
      </div>

      {actionError && (
        <Alert variant="destructive">
          <AlertTitle>{t("admin.actionFailed")}</AlertTitle>
          <AlertDescription>{serverMessage(actionError, t)}</AlertDescription>
        </Alert>
      )}

      {loading ? (
        <LoadingState title={t("directory.loading")} />
      ) : loadError ? (
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refreshConnections()}
        />
      ) : connections.length === 0 ? (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>{t("directory.emptyTitle")}</EmptyTitle>
            <EmptyDescription>{t("directory.emptyDescription")}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="grid gap-4">
          {connections.map((connection) => (
            <Card key={connection.connection_id}>
              <CardHeader>
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <CardTitle>{connection.display_name}</CardTitle>
                      <Badge variant={connection.enabled ? "secondary" : "outline"}>
                        {connection.enabled ? t("directory.enabled") : t("directory.disabled")}
                      </Badge>
                      <Badge variant="outline">{t(`directory.provider.${connection.provider_type}`)}</Badge>
                    </div>
                    <CardDescription className="break-all">
                      {connection.host}:{connection.port} · {connection.tls_mode} · {t("directory.priority")} {connection.priority}
                    </CardDescription>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" variant="outline" onClick={() => openEdit(connection)}>
                      <Pencil data-icon="inline-start" />
                      {t("directory.editConnection")}
                    </Button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="grid gap-3 text-sm sm:grid-cols-3">
                <div>
                  <div className="text-muted-foreground">{t("directory.bindCredential")}</div>
                  <div className="font-medium">{connection.bind_password_configured ? t("directory.configured") : t("directory.notConfigured")}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">{t("directory.certificateAuthority")}</div>
                  <div className="font-medium">{connection.custom_ca_configured ? t("directory.customCa") : t("directory.systemCa")}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">{t("directory.userBaseDn")}</div>
                  <div className="break-all font-medium">{connection.user_base_dn}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}


      <Dialog open={dialogOpen} onOpenChange={(open) => !open && closeDialog()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t("directory.editConnection") : t("directory.addConnection")}</DialogTitle>
            <DialogDescription>{t("directory.connectionDialogDescription")}</DialogDescription>
          </DialogHeader>
          <div className="-mx-4 max-h-[65vh] overflow-y-auto px-4">
            <FieldGroup>
              <FieldSet>
                <FieldLegend>{t("directory.connectionDetails")}</FieldLegend>
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  <TextField id="directory-name" label={t("directory.connectionName")} value={draft.display_name} onChange={(value) => updateDraft("display_name", value)} />
                  <Field><FieldLabel htmlFor="directory-provider">{t("directory.providerType")}</FieldLabel><Select value={draft.provider_type} onValueChange={(value) => changeProviderType(value as DirectoryProviderType)}><SelectTrigger id="directory-provider" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="active_directory">{t("directory.provider.active_directory")}</SelectItem><SelectItem value="ldap">{t("directory.provider.ldap")}</SelectItem></SelectGroup></SelectContent></Select></Field>
                  <TextField id="directory-priority" label={t("directory.priority")} type="number" value={String(draft.priority)} onChange={(value) => updateDraft("priority", Number(value))} />
                  <Field orientation="horizontal"><div><FieldLabel htmlFor="directory-enabled">{t("directory.enabled")}</FieldLabel><FieldDescription>{t("directory.enabledDescription")}</FieldDescription></div><Switch id="directory-enabled" checked={draft.enabled} onCheckedChange={(value) => updateDraft("enabled", value)} /></Field>
                </FieldGroup>
              </FieldSet>
              <FieldSet>
                <FieldLegend>{t("directory.networkSecurity")}</FieldLegend>
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  <TextField id="directory-host" label={t("directory.host")} value={draft.host} onChange={(value) => updateDraft("host", value)} />
                  <TextField id="directory-port" label={t("directory.port")} type="number" value={String(draft.port)} onChange={(value) => updateDraft("port", Number(value))} />
                  <Field><FieldLabel htmlFor="directory-tls">{t("directory.tlsMode")}</FieldLabel><Select value={draft.tls_mode} onValueChange={(value) => changeTlsMode(value as Draft["tls_mode"])}><SelectTrigger id="directory-tls" className="w-full"><SelectValue /></SelectTrigger><SelectContent><SelectGroup><SelectItem value="ldaps">{t("directory.tls.ldaps")}</SelectItem><SelectItem value="start_tls">{t("directory.tls.startTls")}</SelectItem><SelectItem value="plain">{t("directory.tls.plain")}</SelectItem></SelectGroup></SelectContent></Select></Field>
                  <TextField id="directory-connect-timeout" label={t("directory.connectTimeout")} type="number" value={String(draft.connect_timeout_seconds)} onChange={(value) => updateDraft("connect_timeout_seconds", Number(value))} />
                  <TextField id="directory-operation-timeout" label={t("directory.operationTimeout")} type="number" value={String(draft.operation_timeout_seconds)} onChange={(value) => updateDraft("operation_timeout_seconds", Number(value))} />
                  {draft.tls_mode === "plain" ? <Alert variant="destructive" className="sm:col-span-2"><AlertTitle>{t("directory.tls.plain")}</AlertTitle><AlertDescription>{t("directory.plainWarning")}</AlertDescription></Alert> : null}
                  <Field className="sm:col-span-2"><FieldLabel htmlFor="directory-ca">{t("directory.customCaPem")}</FieldLabel><Textarea id="directory-ca" value={customCaPem} disabled={draft.tls_mode === "plain"} onChange={(event) => { setCustomCaPem(event.target.value); if (event.target.value) setClearCustomCa(false); }} placeholder={editing ? t("directory.blankPreserves") : t("directory.systemCaDescription")} className="min-h-28 font-mono text-xs" /><FieldDescription>{draft.tls_mode === "plain" ? t("directory.plainCaUnused") : t("directory.caNeverShown")}</FieldDescription>{editing?.custom_ca_configured && draft.tls_mode !== "plain" ? <Field orientation="horizontal"><Checkbox id="directory-clear-ca" checked={clearCustomCa} onCheckedChange={(checked) => { setClearCustomCa(checked === true); if (checked) setCustomCaPem(""); }} /><FieldLabel htmlFor="directory-clear-ca" className="font-normal">{t("directory.clearCustomCa")}</FieldLabel></Field> : null}</Field>
                </FieldGroup>
              </FieldSet>
              <FieldSet>
                <FieldLegend>{t("directory.bindAndSearch")}</FieldLegend>
                <FieldGroup>
                  <TextField id="directory-bind-dn" label={t("directory.bindDn")} value={draft.bind_dn} onChange={(value) => updateDraft("bind_dn", value)} />
                  <Field><FieldLabel htmlFor="directory-bind-password">{t("directory.bindPassword")}</FieldLabel><Input id="directory-bind-password" type="password" autoComplete="new-password" value={bindPassword} onChange={(event) => { setBindPassword(event.target.value); if (event.target.value) setClearBindPassword(false); }} /><FieldDescription>{editing ? t("directory.blankPreserves") : t("directory.secretNeverShown")}</FieldDescription>{editing?.bind_password_configured ? <Field orientation="horizontal"><Checkbox id="directory-clear-password" checked={clearBindPassword} onCheckedChange={(checked) => { setClearBindPassword(checked === true); if (checked) setBindPassword(""); }} /><FieldLabel htmlFor="directory-clear-password" className="font-normal">{t("directory.clearBindPassword")}</FieldLabel></Field> : null}</Field>
                  <TextField id="directory-base-dn" label={t("directory.userBaseDn")} value={draft.user_base_dn} onChange={(value) => updateDraft("user_base_dn", value)} />
                  <TextField id="directory-filter" label={t("directory.userObjectFilter")} value={draft.user_object_filter} onChange={(value) => updateDraft("user_object_filter", value)} />
                </FieldGroup>
              </FieldSet>
              <FieldSet>
                <FieldLegend>{t("directory.attributeMapping")}</FieldLegend>
                <FieldDescription>{t("directory.attributeMappingDescription")}</FieldDescription>
                <FieldGroup className="grid gap-4 sm:grid-cols-2">
                  {([
                    ["login_attribute", "loginAttribute"], ["stable_id_attribute", "stableIdAttribute"], ["display_name_attribute", "displayNameAttribute"], ["email_attribute", "emailAttribute"], ["groups_attribute", "groupsAttribute"], ["department_attribute", "departmentAttribute"], ["title_attribute", "titleAttribute"], ["employee_id_attribute", "employeeIdAttribute"],
                  ] as const).map(([field, key]) => <TextField key={field} id={`directory-${field}`} label={t(`directory.${key}`)} value={draft[field]} onChange={(value) => updateDraft(field, value)} />)}
                </FieldGroup>
              </FieldSet>
            </FieldGroup>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={closeDialog}>{t("admin.cancel")}</Button>
            <Button
              disabled={!canSave || pendingAction === "save-connection"}
              onClick={() => {
                const connectionId = editing?.connection_id ?? generatedId("directory", draft.display_name);
                void runAction(
                  "save-connection",
                  () => editing
                    ? directoryAdministrationApi.updateConnection({ connectionId, config: draft, bindPassword: bindPassword || undefined, clearBindPassword, customCaPem: customCaPem || undefined, clearCustomCa })
                    : directoryAdministrationApi.createConnection({ connection_id: connectionId, ...draft, bind_password: bindPassword, custom_ca_pem: customCaPem || undefined }),
                  () => {
                    closeDialog();
                  },
                );
              }}
            >
              <ShieldCheck data-icon="inline-start" />
              {t("directory.saveConnection")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

function TextField({ id, label, value, onChange, type = "text" }: { id: string; label: string; value: string; onChange: (value: string) => void; type?: "text" | "number" }) {
  return <Field><FieldLabel htmlFor={id}>{label}</FieldLabel><Input id={id} type={type} value={value} onChange={(event) => onChange(event.target.value)} /></Field>;
}
