import { ChevronRight, FolderKanban, UsersRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../../components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../../components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "../../components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { useIsMobile } from "../../hooks/use-mobile";
import { AdminBreadcrumb, AdminResourceUnavailable } from "../../shared/admin-detail";
import type {
  DocumentTagSummary,
  DocumentTagType,
} from "../../shared/document-contracts";
import {
  activateOnEnterOrSpace,
  clickableSurfaceClassName,
  LoadErrorState,
  LoadingState,
  PageHeader,
  serverMessage,
} from "../../shared/product-ui";
import { ScopeSecondaryNavigation } from "../../shared/scope-secondary-navigation";
import {
  projectKnowledgeRoute,
  teamKnowledgeRoute,
  type AppRoute,
} from "../../shared/routes";
import { workspaceApi } from "../workspace";
import { KnowledgeLibraryFeature } from "./KnowledgeLibraryFeature";

export interface KnowledgeScopeFeatureProps {
  scopeType: DocumentTagType;
  scopeId: string | null;
  workspace?: boolean;
  onNavigate: (route: AppRoute) => void;
}


export function KnowledgeScopeFeature({
  scopeType,
  scopeId,
  workspace = false,
  onNavigate,
}: KnowledgeScopeFeatureProps): ReactNode {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [scopeProjection, setScopeProjection] = useState<{
    requestKey: string;
    scopes: DocumentTagSummary[];
    loading: boolean;
    loadError: string;
  }>({
    requestKey: "",
    scopes: [],
    loading: true,
    loadError: "",
  });
  const [reloadKey, setReloadKey] = useState(0);
  const scopeRequestGeneration = useRef(0);
  const scopeRequestKey = JSON.stringify([scopeType, scopeId, reloadKey]);
  const scopeProjectionCurrent = scopeProjection.requestKey === scopeRequestKey;
  const scopes = scopeProjectionCurrent ? scopeProjection.scopes : [];
  const loading = !scopeProjectionCurrent || scopeProjection.loading;
  const loadError = scopeProjectionCurrent ? scopeProjection.loadError : "";
  const directoryRoute: AppRoute = scopeType === "project" ? "/projects" : "/teams";
  const directoryTitle = t(
    scopeType === "project"
      ? "knowledgeScope.projectsTitle"
      : "knowledgeScope.teamsTitle",
  );
  const directoryDescription = t(
    scopeType === "project"
      ? "knowledgeScope.projectsDescription"
      : "knowledgeScope.teamsDescription",
  );
  const selectedScope = useMemo(
    () => scopes.find((scope) => scope.tag_id === scopeId) ?? null,
    [scopeId, scopes],
  );

  useEffect(() => {
    const generation = ++scopeRequestGeneration.current;
    setScopeProjection({
      requestKey: scopeRequestKey,
      scopes: [],
      loading: true,
      loadError: "",
    });
    workspaceApi
      .workspaceTagScope()
      .then((result) => {
        if (generation !== scopeRequestGeneration.current) return;
        setScopeProjection({
          requestKey: scopeRequestKey,
          scopes: result.tags.filter((scope) => scope.tag_type === scopeType),
          loading: false,
          loadError: "",
        });
      })
      .catch((error) => {
        if (generation !== scopeRequestGeneration.current) return;
        setScopeProjection({
          requestKey: scopeRequestKey,
          scopes: [],
          loading: false,
          loadError:
            error instanceof Error ? error.message : t("admin.listLoadFailed"),
        });
      });
    return () => {
      if (generation === scopeRequestGeneration.current) {
        scopeRequestGeneration.current += 1;
      }
    };
  }, [scopeRequestKey, scopeType, t]);

  function openScope(scope: DocumentTagSummary) {
    onNavigate(
      scope.tag_type === "project"
        ? projectKnowledgeRoute(scope.tag_id)
        : teamKnowledgeRoute(scope.tag_id),
    );
  }

  if (scopeId !== null && loading) {
    return (
      <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
        <PageHeader title={directoryTitle} description={directoryDescription} />
        <LoadingState title={t("knowledgeScope.directoryLoadingTitle")} />
      </section>
    );
  }

  if (scopeId !== null && loadError) {
    return (
      <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
        <PageHeader title={directoryTitle} description={directoryDescription} />
        <LoadErrorState
          title={t("knowledgeScope.directoryLoadFailed")}
          description={serverMessage(loadError, t)}
          retryLabel={t("admin.retry")}
          onRetry={() => setReloadKey((current) => current + 1)}
        />
      </section>
    );
  }

  if (scopeId !== null && selectedScope === null) {
    return (
      <div className="mx-auto w-full max-w-6xl">
        <AdminResourceUnavailable onBack={() => onNavigate(directoryRoute)} />
      </div>
    );
  }

  if (selectedScope) {
    return (
      <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
        <AdminBreadcrumb
          items={[
            { label: directoryTitle, route: directoryRoute },
            { label: selectedScope.label },
            { label: t("knowledgeScope.knowledgeBreadcrumb") },
          ]}
          onNavigate={onNavigate}
        />
        <ScopeSecondaryNavigation
          scopeType={selectedScope.tag_type}
          scopeId={selectedScope.tag_id}
          active="knowledge"
          workspace={workspace}
          onNavigate={onNavigate}
        />
        <KnowledgeLibraryFeature
          key={`${selectedScope.tag_type}:${selectedScope.tag_id}`}
          scope={selectedScope}
        />
      </section>
    );
  }

  const ScopeIcon = scopeType === "project" ? FolderKanban : UsersRound;
  return (
    <section className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <PageHeader title={directoryTitle} description={directoryDescription} />
      <Card>
        <CardHeader>
          <CardTitle>{t("knowledgeScope.availableScopesTitle")}</CardTitle>
          <CardDescription>{t("knowledgeScope.availableScopesDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {loading ? (
            <LoadingState title={t("knowledgeScope.directoryLoadingTitle")} />
          ) : loadError ? (
            <LoadErrorState
              title={t("knowledgeScope.directoryLoadFailed")}
              description={serverMessage(loadError, t)}
              retryLabel={t("admin.retry")}
              onRetry={() => setReloadKey((current) => current + 1)}
            />
          ) :
          scopes.length === 0 ? (
            <Empty className="border">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <ScopeIcon />
                </EmptyMedia>
                <EmptyTitle>{t("knowledgeScope.emptyTitle")}</EmptyTitle>
                <EmptyDescription>
                  {t("knowledgeScope.emptyDescription")}
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : isMobile ? (
            <div className="grid gap-3">
              {scopes.map((scope) => (
                <button
                  type="button"
                  key={scope.tag_id}
                  aria-label={t("knowledgeScope.openScope", { name: scope.label })}
                  className="flex min-h-11 w-full items-center justify-between gap-3 rounded-md border bg-card p-3 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => openScope(scope)}
                >
                  <span className="min-w-0 font-medium">{scope.label}</span>
                  <ChevronRight className="shrink-0" aria-hidden="true" />
                </button>
              ))}
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("knowledgeScope.scopeName")}</TableHead>
                  <TableHead className="text-right">
                    {t("knowledgeLibrary.actions")}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {scopes.map((scope) => (
                  <TableRow
                    key={scope.tag_id}
                    className={clickableSurfaceClassName}
                    role="button"
                    tabIndex={0}
                    aria-label={t("knowledgeScope.openScope", {
                      name: scope.label,
                    })}
                    onClick={() => openScope(scope)}
                    onKeyDown={(event) =>
                      activateOnEnterOrSpace(event, () => openScope(scope))
                    }
                  >
                    <TableCell className="font-medium">{scope.label}</TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={(event) => {
                          event.stopPropagation();
                          openScope(scope);
                        }}
                      >
                        <ChevronRight data-icon="inline-start" />
                        {t("knowledgeScope.openScopeAction")}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </section>
  );
}
