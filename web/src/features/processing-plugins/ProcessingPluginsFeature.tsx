import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, Boxes, Play, RefreshCw, RotateCcw, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import { Button } from "../../components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../../components/ui/card";
import { Checkbox } from "../../components/ui/checkbox";
import { Field, FieldLabel } from "../../components/ui/field";
import { Input } from "../../components/ui/input";
import { Switch } from "../../components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../../components/ui/tabs";
import { OptionSelect } from "../../shared/OptionSelect";
import {
  LoadErrorState,
  LoadingState,
  localizedStatusLabel,
  PageHeader,
  StatusBadge,
  serverMessage,
} from "../../shared/product-ui";
import { processingPluginsApi } from "./api";
import type { ProcessingPluginVersion, ProcessingProfile, ProcessingRun, ProcessingRunDetail } from "./types";

const ref = (plugin: ProcessingPluginVersion) => ({
  plugin_id: plugin.plugin_id, plugin_version: plugin.plugin_version,
  package_digest: plugin.package_digest, runtime_profile: plugin.runtime_profile,
});
const pluginKey = (plugin: ProcessingPluginVersion) =>
  `${plugin.plugin_id}@${plugin.plugin_version}@${plugin.package_digest}`;

export function ProcessingPluginsFeature() {
  const { t } = useTranslation();
  const searchParams = new URLSearchParams(window.location.search);
  const initialTab = searchParams.get("run") || searchParams.get("tab") === "runs"
    ? "runs"
    : "plugins";
  const [activeTab, setActiveTab] = useState<"plugins" | "profiles" | "runs">(initialTab);
  const [plugins, setPlugins] = useState<ProcessingPluginVersion[]>([]);
  const [profiles, setProfiles] = useState<ProcessingProfile[]>([]);
  const [runs, setRuns] = useState<ProcessingRun[]>([]);
  const [runDetail, setRunDetail] = useState<ProcessingRunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [profilesReady, setProfilesReady] = useState(false);
  const [runsReady, setRunsReady] = useState(false);
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [profileId, setProfileId] = useState("");
  const [profileName, setProfileName] = useState("");
  const [mediaType, setMediaType] = useState("application/pdf");
  const [baseId, setBaseId] = useState("");
  const [eligible, setEligible] = useState<string[]>([]);
  const [mandatory, setMandatory] = useState<string[]>([]);
  const [plannerEnabled, setPlannerEnabled] = useState(false);
  const [plannerRoute, setPlannerRoute] = useState("");
  const [maxRegionsPerPlan, setMaxRegionsPerPlan] = useState(100);
  const [maxModulesPerRegion, setMaxModulesPerRegion] = useState(4);
  const [maxTotalInvocations, setMaxTotalInvocations] = useState(500);
  const profilesRequestRef = useRef<Promise<void> | null>(null);
  const runsRequestRef = useRef<Promise<void> | null>(null);

  const verified = plugins.filter((plugin) => plugin.status === "verified");
  const baseParsers = verified.filter((plugin) => plugin.plugin_kind === "base_parser");
  const processors = verified.filter((plugin) => plugin.plugin_kind === "region_processor");
  const processorByKey = useMemo(() => new Map(processors.map((item) => [pluginKey(item), item])), [processors]);

  async function loadPlugins() {
    setLoading(true);
    setError(null);
    try {
      const p = await processingPluginsApi.listPlugins();
      setPlugins(p.items);
      if (!baseId && p.items.some((item) => item.plugin_kind === "base_parser" && item.status === "verified")) {
        setBaseId(pluginKey(p.items.find((item) => item.plugin_kind === "base_parser" && item.status === "verified")!));
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : t("plugins.loadFailed")); }
    finally { setLoading(false); }
  }

  function loadProfiles() {
    if (profilesRequestRef.current) return profilesRequestRef.current;
    const request = (async () => {
      setProfilesLoading(true); setProfilesError(null);
      try {
        const result = await processingPluginsApi.listProfiles();
        setProfiles(result.items); setProfilesReady(true);
      } catch (cause) {
        setProfilesError(cause instanceof Error ? cause.message : t("plugins.loadFailed"));
      } finally {
        setProfilesLoading(false);
      }
    })();
    profilesRequestRef.current = request;
    void request.finally(() => {
      if (profilesRequestRef.current === request) profilesRequestRef.current = null;
    });
    return request;
  }

  function loadRuns() {
    if (runsRequestRef.current) return runsRequestRef.current;
    const request = (async () => {
      setRunsLoading(true); setRunsError(null);
      try {
        const result = await processingPluginsApi.listRuns();
        setRuns(result.items); setRunsReady(true);
        const requestedRun = searchParams.get("run");
        if (requestedRun) setRunDetail(await processingPluginsApi.showRun(requestedRun));
      } catch (cause) {
        setRunsError(cause instanceof Error ? cause.message : t("plugins.loadFailed"));
      } finally {
        setRunsLoading(false);
      }
    })();
    runsRequestRef.current = request;
    void request.finally(() => {
      if (runsRequestRef.current === request) runsRequestRef.current = null;
    });
    return request;
  }

  async function refresh() {
    if (activeTab === "profiles") return loadProfiles();
    if (activeTab === "runs") return loadRuns();
    return loadPlugins();
  }

  useEffect(() => { void loadPlugins(); }, []);
  useEffect(() => {
    if (activeTab === "profiles" && !profilesReady) void loadProfiles();
    if (activeTab === "runs" && !runsReady) void loadRuns();
  }, [activeTab, profilesReady, runsReady]);

  async function action(task: () => Promise<unknown>, success: string) {
    setBusy(true);
    try { await task(); toast.success(success); await refresh(); }
    catch (cause) { toast.error(serverMessage(cause, t)); }
    finally { setBusy(false); }
  }

  function toggleEligible(id: string, checked: boolean) {
    const selectedPlugin = processorByKey.get(id);
    const sameLogicalPlugin = (value: string) =>
      selectedPlugin && processorByKey.get(value)?.plugin_id === selectedPlugin.plugin_id;
    setEligible((current) => checked
      ? [...current.filter((value) => !sameLogicalPlugin(value)), id]
      : current.filter((value) => value !== id));
    setMandatory((current) => current.filter((value) =>
      checked ? !sameLogicalPlugin(value) : value !== id
    ));
  }

  function move(id: string, delta: number) {
    setEligible((current) => {
      const index = current.indexOf(id); const target = index + delta;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = [...current]; [next[index], next[target]] = [next[target], next[index]]; return next;
    });
  }

  function draftRevisionPayload() {
    const base = baseParsers.find((item) => pluginKey(item) === baseId);
    const selected = eligible.map((id) => processorByKey.get(id)).filter(Boolean) as ProcessingPluginVersion[];
    if (!base) return null;
    return {
      accepted_media_types: [mediaType.trim()], base_parser_plugin_ref: ref(base),
      mandatory_processor_plugin_refs: selected.filter((item) => mandatory.includes(pluginKey(item))).map(ref),
      eligible_processor_plugin_refs: selected.map(ref), plugin_priority: selected.map(ref),
      planner_enabled: plannerEnabled, planner_model_route_id: plannerEnabled ? plannerRoute.trim() : null,
      channel_registry_version: "kpel-registry-v0.1", trait_registry_version: "kpel-registry-v0.1",
      max_regions_per_plan: maxRegionsPerPlan,
      max_modules_per_region: maxModulesPerRegion,
      max_total_plugin_invocations: maxTotalInvocations,
      planner_failure_behavior: "mandatory_only",
    };
  }

  async function createProfileAndRevision() {
    const payload = draftRevisionPayload();
    if (!profileId.trim() || !profileName.trim() || !payload) { toast.error(t("plugins.profileRequired")); return; }
    await action(async () => {
      await processingPluginsApi.createProfile(profileId.trim(), profileName.trim());
      await processingPluginsApi.createRevision(profileId.trim(), 0, payload);
      setProfileId(""); setProfileName(""); setEligible([]); setMandatory([]);
    }, t("plugins.profileCreated"));
  }

  async function createNextRevision(profile: ProcessingProfile) {
    const payload = draftRevisionPayload();
    if (!payload) { toast.error(t("plugins.profileRequired")); return; }
    const current = Math.max(0, ...profile.revisions.map((item) => item.revision));
    await action(() => processingPluginsApi.createRevision(profile.profile_id, current, payload), t("plugins.revisionCreated"));
  }

  const pageHeader = (
    <div className="flex items-start justify-between gap-4">
      <PageHeader title={t("plugins.title")} description={t("plugins.description")} />
      <Button variant="outline" onClick={() => void refresh()} disabled={busy || loading}>
        <RefreshCw />
        {t("plugins.refresh")}
      </Button>
    </div>
  );

  if (loading) {
    return (
      <section className="flex flex-col gap-5">
        {pageHeader}
        <LoadingState
          title={t("plugins.loadingTitle")}
        />
      </section>
    );
  }

  if (error) {
    return (
      <section className="flex flex-col gap-5">
        {pageHeader}
        <LoadErrorState
          title={t("admin.listLoadFailed")}
          description={serverMessage(error, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => void refresh()}
        />
      </section>
    );
  }

  return <section className="flex flex-col gap-5">
    {pageHeader}
    <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as typeof activeTab)} className="gap-5">
      <TabsList variant="line" aria-label={t("plugins.tabsLabel")}>
        <TabsTrigger value="plugins"><Boxes />{t("plugins.pluginsTab")}</TabsTrigger>
        <TabsTrigger value="profiles">{t("plugins.profilesTab")}</TabsTrigger>
        <TabsTrigger value="runs">{t("plugins.runsTab")}</TabsTrigger>
      </TabsList>

      <TabsContent value="plugins" className="flex flex-col gap-4">
        <Card><CardHeader><CardTitle>{t("plugins.installTitle")}</CardTitle><CardDescription>{t("plugins.installDescription")}</CardDescription></CardHeader>
          <CardContent><Input type="file" accept=".atlas-plugin" aria-label={t("plugins.packageFile")} disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0]; if (file) void action(() => processingPluginsApi.upload(file), t("plugins.uploaded"));
          }} /></CardContent></Card>
        <PluginTable plugins={plugins} busy={busy} onAction={(plugin, next) => void action(() => processingPluginsApi.mutatePlugin(plugin, next), t("plugins.actionApplied"))} />
      </TabsContent>

      <TabsContent value="profiles" className="flex flex-col gap-4">
        {profilesError && profilesReady && (
          <Alert variant="destructive">
            <AlertTitle>{t("admin.listLoadFailed")}</AlertTitle>
            <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
              <span>{serverMessage(profilesError, t)}</span>
              <Button size="sm" variant="outline" onClick={() => void loadProfiles()}>
                {t("admin.retry")}
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {profilesLoading && !profilesReady ? (
          <LoadingState title={t("plugins.loadingTitle")} />
        ) : profilesError && !profilesReady ? (
          <LoadErrorState title={t("admin.listLoadFailed")} description={serverMessage(profilesError, t)} retryLabel={t("admin.retry")} onRetry={() => void loadProfiles()} />
        ) : <>
        <Card><CardHeader><CardTitle>{t("plugins.newProfile")}</CardTitle><CardDescription>{t("plugins.newProfileDescription")}</CardDescription></CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-2">
            <Field><FieldLabel>{t("plugins.profileId")}</FieldLabel><Input value={profileId} onChange={(e) => setProfileId(e.target.value)} /></Field>
            <Field><FieldLabel>{t("plugins.profileName")}</FieldLabel><Input value={profileName} onChange={(e) => setProfileName(e.target.value)} /></Field>
            <Field><FieldLabel>{t("plugins.mediaType")}</FieldLabel><Input value={mediaType} onChange={(e) => setMediaType(e.target.value)} /></Field>
            <Field><FieldLabel>{t("plugins.baseParser")}</FieldLabel><OptionSelect value={baseId} onValueChange={setBaseId} options={baseParsers.map((item) => ({ value: pluginKey(item), label: `${item.plugin_id} ${item.plugin_version}` }))} /></Field>
            <div className="md:col-span-2"><div className="mb-2 text-sm font-medium">{t("plugins.processorsPriority")}</div><div className="grid gap-2">
              {processors.map((plugin) => { const key = pluginKey(plugin); const selected = eligible.includes(key); return <div key={key} className="flex items-center gap-3 rounded-md border p-3">
                <Checkbox checked={selected} onCheckedChange={(value) => toggleEligible(key, value === true)} aria-label={`${t("plugins.eligible")} ${plugin.plugin_id} ${plugin.plugin_version}`} />
                <div className="min-w-0 flex-1"><div className="font-medium">{plugin.plugin_id}</div><div className="text-xs text-muted-foreground">{plugin.plugin_version} · {plugin.runtime_profile}</div></div>
                <label className="flex items-center gap-2 text-sm"><Checkbox disabled={!selected} checked={mandatory.includes(key)} onCheckedChange={(value) => setMandatory((current) => value === true ? [...new Set([...current, key])] : current.filter((id) => id !== key))} />{t("plugins.mandatory")}</label>
                <Button size="icon-sm" variant="ghost" disabled={!selected} onClick={() => move(key, -1)}><ArrowUp /></Button><Button size="icon-sm" variant="ghost" disabled={!selected} onClick={() => move(key, 1)}><ArrowDown /></Button>
              </div>; })}
            </div></div>
            <Field orientation="horizontal"><Switch checked={plannerEnabled} onCheckedChange={setPlannerEnabled} /><FieldLabel>{t("plugins.enablePlanner")}</FieldLabel></Field>
            <Field><FieldLabel>{t("plugins.plannerRoute")}</FieldLabel><Input disabled={!plannerEnabled} value={plannerRoute} onChange={(e) => setPlannerRoute(e.target.value)} /></Field>
            <Field><FieldLabel>{t("plugins.maxRegionsPerPlan")}</FieldLabel><Input aria-label={t("plugins.maxRegionsPerPlan")} type="number" min={1} value={maxRegionsPerPlan} onChange={(e) => setMaxRegionsPerPlan(Math.max(1, Number(e.target.value) || 1))} /></Field>
            <Field><FieldLabel>{t("plugins.maxModulesPerRegion")}</FieldLabel><Input aria-label={t("plugins.maxModulesPerRegion")} type="number" min={1} value={maxModulesPerRegion} onChange={(e) => setMaxModulesPerRegion(Math.max(1, Number(e.target.value) || 1))} /></Field>
            <Field><FieldLabel>{t("plugins.maxTotalInvocations")}</FieldLabel><Input aria-label={t("plugins.maxTotalInvocations")} type="number" min={1} value={maxTotalInvocations} onChange={(e) => setMaxTotalInvocations(Math.max(1, Number(e.target.value) || 1))} /></Field>
            <div className="md:col-span-2"><Button disabled={busy} onClick={() => void createProfileAndRevision()}>{t("plugins.createProfile")}</Button></div>
          </CardContent></Card>
        {profiles.map((profile) => <Card key={profile.profile_id}><CardHeader><div className="flex items-start justify-between gap-3"><div><CardTitle>{profile.display_name}</CardTitle><CardDescription>{profile.profile_id}</CardDescription></div><Button size="sm" variant="outline" disabled={busy} onClick={() => void createNextRevision(profile)}>{t("plugins.createRevision")}</Button></div></CardHeader><CardContent className="grid gap-2">
          {profile.revisions.map((revision) => <div key={revision.revision} className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3"><div><div className="font-medium">{t("plugins.revision", { revision: revision.revision })}</div><div className="text-sm text-muted-foreground">{revision.accepted_media_types.join(", ")} · {revision.base_parser_plugin_ref.plugin_id}</div></div><div className="flex items-center gap-2"><StatusBadge semantic={profileRevisionSemantic(revision.status)} label={localizedStatusLabel(revision.status, t)} />{["draft", "deprecated"].includes(revision.status) && <Button size="sm" disabled={busy} onClick={() => void action(() => processingPluginsApi.activate(profile.profile_id, revision.revision, Math.max(...profile.revisions.map((item) => item.revision))), t("plugins.activated"))}><Play />{t("plugins.activate")}</Button>}</div></div>)}
        </CardContent></Card>)}
        </>}
      </TabsContent>

      <TabsContent value="runs">
      {runsError && runsReady && (
        <Alert variant="destructive">
          <AlertTitle>{t("admin.listLoadFailed")}</AlertTitle>
          <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
            <span>{serverMessage(runsError, t)}</span>
            <Button size="sm" variant="outline" onClick={() => void loadRuns()}>
              {t("admin.retry")}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      {runsLoading && !runsReady ? (
        <LoadingState title={t("plugins.loadingTitle")} />
      ) : runsError && !runsReady ? (
        <LoadErrorState title={t("admin.listLoadFailed")} description={serverMessage(runsError, t)} retryLabel={t("admin.retry")} onRetry={() => void loadRuns()} />
      ) : <Table><TableHeader><TableRow><TableHead>{t("plugins.run")}</TableHead><TableHead>{t("plugins.document")}</TableHead><TableHead>{t("plugins.profile")}</TableHead><TableHead>{t("plugins.status")}</TableHead><TableHead /></TableRow></TableHeader><TableBody>
        {runs.map((run) => <TableRow key={run.run_id}><TableCell className="font-mono text-xs">{run.run_id}</TableCell><TableCell>{run.document_id}</TableCell><TableCell>{run.profile_id} {t("plugins.revisionShort", { revision: run.profile_revision })}</TableCell><TableCell><StatusBadge semantic={processingRunSemantic(run.status)} label={localizedStatusLabel(run.status, t)} />{run.warning_codes.length > 0 && <div className="mt-1 text-xs text-muted-foreground">{run.warning_codes.join(", ")}</div>}{runDetail?.run_id === run.run_id && <div className="mt-2 max-w-xl rounded-md bg-muted p-2 text-xs"><div>{t("plugins.invocations")}: {runDetail.invocations.map((item) => `${item.plugin_ref.plugin_id} (${item.status})`).join(", ") || "—"}</div><div>{t("plugins.routingDecisions")}: {runDetail.routing_decisions.length}</div><div>{t("plugins.failedChannels")}: {runDetail.trace?.payload.failed_channels?.join(", ") || "—"}</div></div>}</TableCell><TableCell className="text-right"><div className="flex justify-end gap-2"><Button size="sm" variant="ghost" disabled={busy} onClick={() => void action(async () => setRunDetail(await processingPluginsApi.showRun(run.run_id)), t("plugins.runLoaded"))}>{t("plugins.inspect")}</Button>{run.status === "failed" && <Button size="sm" variant="outline" disabled={busy} onClick={() => void action(() => processingPluginsApi.retryRun(run.run_id), t("plugins.retryCompleted"))}><RotateCcw />{t("plugins.retry")}</Button>}</div></TableCell></TableRow>)}
      </TableBody></Table>}
      </TabsContent>
    </Tabs>
  </section>;
}

function PluginTable({ plugins, busy, onAction }: { plugins: ProcessingPluginVersion[]; busy: boolean; onAction: (plugin: ProcessingPluginVersion, action: "validate" | "canary" | "disable") => void }) {
  const { t } = useTranslation();
  return <Table><TableHeader><TableRow><TableHead>{t("plugins.plugin")}</TableHead><TableHead>{t("plugins.runtime")}</TableHead><TableHead>{t("plugins.packageIntegrity")}</TableHead><TableHead>{t("plugins.trust")}</TableHead><TableHead>{t("plugins.status")}</TableHead><TableHead /></TableRow></TableHeader><TableBody>
    {plugins.map((plugin) => <TableRow key={`${plugin.plugin_id}:${plugin.plugin_version}`}><TableCell><div className="font-medium">{plugin.plugin_id}</div><div className="text-xs text-muted-foreground">{plugin.plugin_version} · {plugin.plugin_kind}</div></TableCell><TableCell>{plugin.runtime_profile}</TableCell><TableCell><div className="max-w-52 truncate font-mono text-xs" title={plugin.package_digest}>{plugin.package_digest}</div><div className="text-xs text-muted-foreground">{t("plugins.signatureKey")}: {plugin.descriptor?.signature_key_id || "—"} · {t("plugins.sbom")}: {plugin.descriptor?.sbom_present ? plugin.descriptor.sbom_spdx_version || "SPDX" : "—"}</div><div className="text-xs text-muted-foreground">{t("plugins.license")}: {plugin.descriptor?.license_expression || "—"}</div></TableCell><TableCell>{plugin.trust_provenance}</TableCell><TableCell><StatusBadge semantic={pluginStatusSemantic(plugin.status)} label={localizedStatusLabel(plugin.status, t)} />{plugin.diagnostic_code && <div className="mt-1 text-xs text-muted-foreground">{plugin.diagnostic_code}</div>}</TableCell><TableCell className="text-right"><div className="flex justify-end gap-2">{plugin.status === "uploaded" && <Button size="sm" disabled={busy} onClick={() => onAction(plugin, "validate")}>{t("plugins.validate")}</Button>}{plugin.status === "verified" && <><Button size="sm" variant="outline" disabled={busy} onClick={() => onAction(plugin, "canary")}>{t("plugins.canary")}</Button><Button size="sm" variant="outline" disabled={busy || plugin.active} onClick={() => onAction(plugin, "disable")}>{t("plugins.disable")}</Button></>}</div></TableCell></TableRow>)}
  </TableBody></Table>;
}

function profileRevisionSemantic(status: "draft" | "canary" | "active" | "deprecated") {
  if (status === "active") return "success" as const;
  if (status === "canary") return "progress" as const;
  if (status === "deprecated") return "inactive" as const;
  return "attention" as const;
}

function processingRunSemantic(status: string) {
  if (status === "completed" || status === "succeeded") return "success" as const;
  if (status === "failed") return "failure" as const;
  if (status === "queued" || status === "running" || status === "processing") return "progress" as const;
  return "unknown" as const;
}

function pluginStatusSemantic(status: "uploaded" | "validating" | "quarantined" | "verified" | "disabled" | "rejected") {
  if (status === "verified") return "success" as const;
  if (status === "uploaded" || status === "validating") return "progress" as const;
  if (status === "quarantined") return "attention" as const;
  if (status === "rejected") return "failure" as const;
  return "inactive" as const;
}
