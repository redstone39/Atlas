import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { PDFDocumentLoadingTask } from "pdfjs-dist";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { Alert, AlertDescription, AlertTitle } from "../../components/ui/alert";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../../components/ui/dialog";
import { Spinner } from "../../components/ui/spinner";
import { cn } from "../../lib/utils";
import type { DeclaredEvidencePreview } from "./api";

export type EvidenceViewerWatermark = {
  displayName: string | null;
  actorId: string | null;
  displayedAt: string;
};

export function EvidenceViewerDialog({
  evidence,
  loading,
  onClose,
  watermark = null,
}: {
  evidence: DeclaredEvidencePreview | null;
  loading: boolean;
  onClose: () => void;
  watermark?: EvidenceViewerWatermark | null;
}) {
  const { t } = useTranslation();
  const [pageUrl, setPageUrl] = useState<string | null>(null);
  const pagePreview = !loading && evidence?.kind === "page" ? evidence : null;

  useEffect(() => {
    setPageUrl(null);
    if (!pagePreview || pagePreview.mediaType !== "image/png") return;
    const objectUrl = URL.createObjectURL(pagePreview.blob);
    setPageUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [pagePreview]);

  return (
    <Dialog
      open={loading || evidence !== null}
      onOpenChange={(open) => {
        if (!open && !loading) onClose();
      }}
    >
      <DialogContent
        className="max-h-[85vh] overflow-y-auto"
      >
        <DialogHeader>
          <DialogTitle>{t("citationViewer.title")}</DialogTitle>
          <DialogDescription>{t("citationViewer.description")}</DialogDescription>
        </DialogHeader>
        {watermark && !loading && evidence && (
          <p className="text-xs text-muted-foreground">
            {t("citationViewer.watermarkDisclosure")}
          </p>
        )}
        {loading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Spinner />
            {t("citationViewer.loading")}
          </div>
        ) : evidence?.kind === "excerpt" ? (
          <WatermarkedEvidence watermark={watermark}>
            <div className="flex flex-col gap-4">
              <div className="text-sm font-medium">{evidence.evidence.locator_label}</div>
              {evidence.evidence.snippet && (
                <div className="rounded-md bg-muted p-3 text-sm">
                  {evidence.evidence.snippet}
                </div>
              )}
              <div className="whitespace-pre-wrap text-sm leading-6">
                {evidence.evidence.content}
              </div>
            </div>
          </WatermarkedEvidence>
        ) : pagePreview?.mediaType === "application/pdf" ? (
          <PdfEvidencePage blob={pagePreview.blob} watermark={watermark} />
        ) : pagePreview?.mediaType === "image/png" && pageUrl ? (
          <WatermarkedEvidence watermark={watermark}>
            <img
              className="h-auto max-h-[70vh] w-full rounded-md border object-contain"
              src={pageUrl}
              alt={t("citationViewer.imagePage")}
            />
          </WatermarkedEvidence>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PdfEvidencePage({
  blob,
  watermark,
}: {
  blob: Blob;
  watermark: EvidenceViewerWatermark | null;
}) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  useEffect(() => {
    let loadingTask: PDFDocumentLoadingTask | null = null;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
    let cancelled = false;
    setStatus("loading");

    void (async () => {
      try {
        const bytes = await blob.arrayBuffer();
        if (cancelled) return;
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;
        loadingTask = pdfjs.getDocument({ data: bytes });
        const documentProxy = await loadingTask.promise;
        if (cancelled) return;
        const page = await documentProxy.getPage(1);
        if (cancelled) return;
        const viewport = page.getViewport({ scale: 1.35 });
        const canvas = canvasRef.current;
        if (!canvas || cancelled) return;
        const ratio = window.devicePixelRatio || 1;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        const context = canvas.getContext("2d");
        if (!context) throw new Error("canvas_unavailable");
        renderTask = page.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        });
        await renderTask.promise;
        if (!cancelled) setStatus("ready");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
      renderTask?.cancel();
      void loadingTask?.destroy();
    };
  }, [blob]);

  if (status === "error") {
    return (
      <Alert variant="destructive">
        <AlertTitle>{t("citationViewer.contentFailed")}</AlertTitle>
        <AlertDescription>{t("citationViewer.canvasUnavailable")}</AlertDescription>
      </Alert>
    );
  }

  return (
    <WatermarkedEvidence
      className="min-h-[65vh] overflow-auto rounded-md border bg-muted"
      watermark={status === "ready" ? watermark : null}
    >
      {status === "loading" && (
        <div className="absolute inset-0 z-10 flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Spinner />
          {t("citationViewer.contentLoading")}
        </div>
      )}
      <canvas
        ref={canvasRef}
        aria-label={t("citationViewer.pdfPage")}
        className={cn("mx-auto bg-background", status !== "ready" && "invisible")}
      />
    </WatermarkedEvidence>
  );
}

function WatermarkedEvidence({
  children,
  className,
  watermark,
}: {
  children: ReactNode;
  className?: string;
  watermark: EvidenceViewerWatermark | null;
}) {
  return (
    <div className={cn("relative overflow-hidden", className)}>
      {children}
      {watermark && <WatermarkOverlay watermark={watermark} />}
    </div>
  );
}

function WatermarkOverlay({
  watermark,
}: {
  watermark: EvidenceViewerWatermark;
}) {
  const { t } = useTranslation();
  const identity = [watermark.displayName?.trim(), watermark.actorId?.trim()]
    .filter(Boolean)
    .join(" · ");
  const label = [
    identity || t("citationViewer.watermarkGeneric"),
    watermark.displayedAt,
  ].join(" · ");

  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-20 grid select-none grid-cols-2 content-around overflow-hidden opacity-20 sm:grid-cols-3"
      data-testid="evidence-watermark"
    >
      {Array.from({ length: 12 }, (_, index) => (
        <span
          className="-rotate-12 whitespace-nowrap px-3 text-center text-[11px] font-semibold tracking-wide text-foreground"
          key={index}
        >
          {label}
        </span>
      ))}
    </div>
  );
}
