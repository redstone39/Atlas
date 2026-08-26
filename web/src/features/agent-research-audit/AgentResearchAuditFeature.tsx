import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "../../components/ui/empty";
import { AdminResourceUnavailable } from "../../shared/admin-detail";
import {
  LoadErrorState,
  LoadingState,
  serverMessage,
} from "../../shared/product-ui";
import {
  adminAgentResearchAuditRoute,
  matchAppRoute,
  type AppRoute,
} from "../../shared/routes";
import { ApiError } from "../../shared/user-messages";
import {
  EvidenceViewerDialog,
  type DeclaredEvidencePreview,
} from "../workspace";
import { agentResearchAuditApi } from "./api";
import {
  AuditShell,
  ResearchDetail,
  ResearchDirectory,
  ResearchRuntime,
} from "./AgentResearchAuditPresentation";
import type {
  AgentResearchAuditDetail,
  AgentResearchAuditListItem,
  AgentResearchRuntimeDetail,
  ResearchEvidenceDescriptor,
} from "./types";

export function AgentResearchAuditFeature({
  route,
  onNavigate,
}: {
  route: AppRoute;
  onNavigate: (route: AppRoute) => void;
}) {
  const { t } = useTranslation();
  const match = matchAppRoute(route);
  const [items, setItems] = useState<AgentResearchAuditListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [listInitialized, setListInitialized] = useState(false);
  const [detail, setDetail] = useState<AgentResearchAuditDetail | null>(null);
  const [runtime, setRuntime] = useState<AgentResearchRuntimeDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [unavailableRoute, setUnavailableRoute] = useState<AppRoute | null>(null);
  const [evidence, setEvidence] = useState<DeclaredEvidencePreview | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const generation = useRef(0);
  const evidenceGeneration = useRef(0);

  const section = match.kind === "admin-audit-agent-research" ? match.section : null;
  const researchId = match.kind === "admin-audit-agent-research"
    ? match.researchId
    : undefined;

  useEffect(() => {
    if (section === null) return;
    const requestGeneration = ++generation.current;
    evidenceGeneration.current += 1;
    setError("");
    setUnavailableRoute(null);
    setEvidence(null);
    setEvidenceLoading(false);
    if (section === "list" && listInitialized) {
      setLoading(false);
      return;
    }
    if (section === "list") {
      setDetail(null);
      setRuntime(null);
      setLoading(true);
      void agentResearchAuditApi.list()
        .then((result) => {
          if (generation.current !== requestGeneration) return;
          setItems(result.items);
          setNextCursor(result.next_cursor);
          setListInitialized(true);
        })
        .catch((caught) => {
          if (generation.current !== requestGeneration) return;
          setError(serverMessage(caught, t));
        })
        .finally(() => {
          if (generation.current === requestGeneration) setLoading(false);
        });
      return;
    }
    if (!researchId) return;
    setDetail(null);
    setRuntime(null);
    setLoading(true);
    const request = section === "runtime"
      ? agentResearchAuditApi.runtime(researchId)
      : agentResearchAuditApi.detail(researchId);
    void request
      .then((result) => {
        if (generation.current !== requestGeneration) return;
        if (section === "runtime") {
          setRuntime(result as AgentResearchRuntimeDetail);
        } else {
          setDetail(result as AgentResearchAuditDetail);
        }
      })
      .catch((caught) => {
        if (generation.current !== requestGeneration) return;
        if (caught instanceof ApiError && (caught.status === 403 || caught.status === 404)) {
          setUnavailableRoute(route);
        } else {
          setError(serverMessage(caught, t));
        }
      })
      .finally(() => {
        if (generation.current === requestGeneration) setLoading(false);
      });
  }, [researchId, retryToken, route, section, t]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await agentResearchAuditApi.list(nextCursor);
      setItems((current) => [...current, ...result.items]);
      setNextCursor(result.next_cursor);
    } catch (caught) {
      toast.error(serverMessage(caught, t));
    } finally {
      setLoadingMore(false);
    }
  }

  async function openEvidence(
    descriptor: ResearchEvidenceDescriptor,
    representation: "text" | "visual" | "native",
  ) {
    if (!researchId) return;
    const requestGeneration = ++evidenceGeneration.current;
    setEvidence(null);
    setEvidenceLoading(true);
    try {
      const result = await agentResearchAuditApi.evidence(
        researchId,
        descriptor,
        representation,
      );
      if (evidenceGeneration.current === requestGeneration) {
        setEvidence(result);
      }
    } catch (caught) {
      if (evidenceGeneration.current === requestGeneration) {
        toast.error(serverMessage(caught, t));
      }
    } finally {
      if (evidenceGeneration.current === requestGeneration) {
        setEvidenceLoading(false);
      }
    }
  }

  if (section === null) return null;
  const directoryRoute = adminAgentResearchAuditRoute();
  if (unavailableRoute === route) {
    return <AdminResourceUnavailable onBack={() => onNavigate(directoryRoute)} />;
  }
  if (loading) {
    return (
      <AuditShell onNavigate={onNavigate} section={section}>
        <LoadingState title={t("agentResearchAudit.loadingTitle")} />
      </AuditShell>
    );
  }
  if (error) {
    return (
      <AuditShell onNavigate={onNavigate} section={section}>
        <LoadErrorState
          title={t("agentResearchAudit.loadFailedTitle")}
          description={error}
          retryLabel={t("common.retry")}
          onRetry={() => {
            if (section === "list") setListInitialized(false);
            setRetryToken((current) => current + 1);
          }}
        />
      </AuditShell>
    );
  }

  return (
    <AuditShell onNavigate={onNavigate} section={section}>
      {section === "list" ? (
        <ResearchDirectory
          items={items}
          nextCursor={nextCursor}
          loadingMore={loadingMore}
          onLoadMore={() => void loadMore()}
          onOpen={(id) => onNavigate(adminAgentResearchAuditRoute(id))}
        />
      ) : section === "detail" && detail ? (
        <ResearchDetail
          detail={detail}
          onOpenRuntime={() => onNavigate(
            adminAgentResearchAuditRoute(detail.research_id, "runtime"),
          )}
          onOpenEvidence={(descriptor, representation) =>
            void openEvidence(descriptor, representation)}
        />
      ) : section === "runtime" && runtime ? (
        <ResearchRuntime runtime={runtime} />
      ) : (
        <Empty className="border">
          <EmptyHeader>
            <EmptyTitle>{t("agentResearchAudit.emptyTitle")}</EmptyTitle>
            <EmptyDescription>{t("agentResearchAudit.emptyDescription")}</EmptyDescription>
          </EmptyHeader>
        </Empty>
      )}
      <EvidenceViewerDialog
        evidence={evidence}
        loading={evidenceLoading}
        onClose={() => setEvidence(null)}
      />
    </AuditShell>
  );
}

