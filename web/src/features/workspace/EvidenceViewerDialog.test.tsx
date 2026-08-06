import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "../../i18n";
import type { DeclaredEvidencePreview } from "./api";
import {
  EvidenceViewerDialog,
  type EvidenceViewerWatermark,
} from "./EvidenceViewerDialog";

const pdfMocks = vi.hoisted(() => ({
  getDocument: vi.fn(),
  workerOptions: { workerSrc: "" },
}));

vi.mock("pdfjs-dist", () => ({
  getDocument: pdfMocks.getDocument,
  GlobalWorkerOptions: pdfMocks.workerOptions,
}));

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "pdf-worker",
}));

const originalCreateObjectUrl = Object.getOwnPropertyDescriptor(URL, "createObjectURL");
const originalRevokeObjectUrl = Object.getOwnPropertyDescriptor(URL, "revokeObjectURL");
const originalCanvasGetContext = Object.getOwnPropertyDescriptor(
  HTMLCanvasElement.prototype,
  "getContext",
);

const excerpt: DeclaredEvidencePreview = {
  kind: "excerpt",
  evidence: {
    evidence_handle: "kh_evidence_a",
    locator_label: "Page 2",
    snippet: "Authorized excerpt",
    content: "Authorized evidence content",
    modality: "text",
  },
};

const watermark: EvidenceViewerWatermark = {
  displayName: "Workspace User",
  actorId: "user-a",
  displayedAt: "2026-07-29T10:20:30.000Z",
};

let pdfRender: ReturnType<typeof mockSuccessfulPdfRender>;

beforeEach(async () => {
  await i18n.changeLanguage("en");
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => ({})),
  });
  pdfMocks.getDocument.mockReset();
  pdfMocks.workerOptions.workerSrc = "";
  pdfRender = mockSuccessfulPdfRender();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  restoreDescriptor(URL, "createObjectURL", originalCreateObjectUrl);
  restoreDescriptor(URL, "revokeObjectURL", originalRevokeObjectUrl);
  restoreDescriptor(
    HTMLCanvasElement.prototype,
    "getContext",
    originalCanvasGetContext,
  );
});

describe("EvidenceViewerDialog", () => {
  it("watermarks the existing excerpt presentation without creating an object URL", () => {
    render(
      <EvidenceViewerDialog
        evidence={excerpt}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );

    expect(screen.getByText("Page 2")).toBeInTheDocument();
    expect(screen.getByText("Authorized excerpt")).toBeInTheDocument();
    expect(screen.getByText("Authorized evidence content")).toBeInTheDocument();
    expectWatermark("Workspace User · user-a · 2026-07-29T10:20:30.000Z");
    const dialog = screen.getByRole("dialog");
    const content = dialog.querySelector(
      '[data-slot="evidence-viewer-content"]',
    ) as HTMLElement;
    expect(dialog).toHaveClass("overflow-hidden");
    expect(dialog).not.toHaveClass("overflow-y-auto");
    expect(content).toHaveClass("min-h-0", "overflow-y-auto", "overscroll-contain");
    expect(content).toContainElement(screen.getByText("Authorized evidence content"));
    expect(content).not.toContainElement(dialog.querySelector('[data-slot="dialog-header"]'));
    expect(screen.queryByText(
      "Review the evidence you are currently authorized to open.",
    )).not.toBeInTheDocument();
    expect(screen.queryByText(
      /visual watermark is for identification only/,
    )).not.toBeInTheDocument();
    expect(URL.createObjectURL).not.toHaveBeenCalled();
  });

  it("renders an accessible PDF canvas without native viewer or download controls", async () => {
    render(
      <EvidenceViewerDialog
        evidence={pdfPreview()}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );

    const canvas = screen.getByLabelText("Authorized PDF evidence page");
    await waitFor(() => expect(canvas).not.toHaveClass("invisible"));
    expect(screen.getByRole("dialog")).toHaveAttribute("data-size", "wide");
    expect(canvas.tagName.toLowerCase()).toBe("canvas");
    expect(canvas).toHaveClass("max-w-full");
    expect(canvas).toHaveStyle({ width: "400px", height: "auto" });
    expect(pdfMocks.getDocument).toHaveBeenCalledOnce();
    expect(pdfMocks.workerOptions.workerSrc).toBe("pdf-worker");
    expect(pdfRender.getPage).toHaveBeenCalledWith(1);
    expect(pdfRender.getViewport).toHaveBeenCalledWith({ scale: 1.35 });
    expectWatermark("Workspace User · user-a · 2026-07-29T10:20:30.000Z");
    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(document.querySelector("iframe, object, embed, a")).toBeNull();
    expect(screen.queryByRole("button", { name: /download|print|open/i })).not.toBeInTheDocument();
  });

  it("watermarks an image preview and revokes its object URL on unmount", async () => {
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:image-preview");
    const { unmount } = render(
      <EvidenceViewerDialog
        evidence={imagePreview()}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );

    expect(await screen.findByAltText("Authorized image evidence page")).toHaveAttribute(
      "src",
      "blob:image-preview",
    );
    expectWatermark("Workspace User · user-a · 2026-07-29T10:20:30.000Z");

    unmount();
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:image-preview");
  });

  it("uses a generic watermark when authenticated actor details are unavailable", () => {
    render(
      <EvidenceViewerDialog
        evidence={excerpt}
        loading={false}
        onClose={vi.fn()}
        watermark={{
          displayName: null,
          actorId: null,
          displayedAt: "2026-07-29T10:20:30.000Z",
        }}
      />,
    );

    expectWatermark("Atlas internal evidence preview · 2026-07-29T10:20:30.000Z");
    expect(screen.getByText("Authorized evidence content")).toBeInTheDocument();
  });

  it("shows a safe error without a watermark when PDF rendering fails", async () => {
    pdfMocks.getDocument.mockReturnValue({
      promise: Promise.reject(new Error("unsafe parser detail")),
      destroy: vi.fn(),
    });
    render(
      <EvidenceViewerDialog
        evidence={pdfPreview()}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );

    expect(await screen.findByText("Evidence content unavailable")).toBeInTheDocument();
    expect(screen.getByText("This browser cannot render the evidence page.")).toBeInTheDocument();
    expect(screen.queryByText("unsafe parser detail")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-watermark")).not.toBeInTheDocument();
  });

  it("cancels PDF rendering when the preview is replaced", async () => {
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:image-preview");
    const { rerender } = render(
      <EvidenceViewerDialog
        evidence={pdfPreview()}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );
    await waitFor(() =>
      expect(screen.getByLabelText("Authorized PDF evidence page")).not.toHaveClass("invisible"),
    );

    rerender(
      <EvidenceViewerDialog
        evidence={imagePreview()}
        loading={false}
        onClose={vi.fn()}
        watermark={watermark}
      />,
    );
    await screen.findByAltText("Authorized image evidence page");

    expect(pdfRender.cancel).toHaveBeenCalledOnce();
    expect(pdfRender.destroy).toHaveBeenCalledOnce();
  });

  it("cleans up the image URL and closes without a viewer-specific action", async () => {
    vi.mocked(URL.createObjectURL).mockReturnValue("blob:image-preview");
    const onClose = vi.fn();
    const Harness = () => {
      const [evidence, setEvidence] = useState<DeclaredEvidencePreview | null>(
        imagePreview(),
      );
      return (
        <EvidenceViewerDialog
          evidence={evidence}
          loading={false}
          onClose={() => {
            setEvidence(null);
            onClose();
          }}
          watermark={watermark}
        />
      );
    };
    render(<Harness />);
    await screen.findByAltText("Authorized image evidence page");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
    expect(URL.revokeObjectURL).toHaveBeenCalledOnce();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:image-preview");
  });
});

function mockSuccessfulPdfRender() {
  const cancel = vi.fn();
  const render = vi.fn(() => ({
    cancel,
    promise: Promise.resolve(),
  }));
  const getViewport = vi.fn(() => ({ width: 400, height: 600 }));
  const getPage = vi.fn(async () => ({
    getViewport,
    render,
  }));
  const destroy = vi.fn(async () => undefined);
  pdfMocks.getDocument.mockReturnValue({
    promise: Promise.resolve({ getPage }),
    destroy,
  });
  return { cancel, destroy, getPage, getViewport, render };
}

function previewBlob(content: string) {
  const blob = new Blob([content]);
  Object.defineProperty(blob, "arrayBuffer", {
    configurable: true,
    value: vi.fn(async () => new TextEncoder().encode(content).buffer),
  });
  return blob;
}

function pdfPreview(): DeclaredEvidencePreview {
  return {
    kind: "page",
    mediaType: "application/pdf",
    blob: previewBlob("pdf"),
  };
}

function imagePreview(): DeclaredEvidencePreview {
  return {
    kind: "page",
    mediaType: "image/png",
    blob: previewBlob("png"),
  };
}

function expectWatermark(label: string) {
  const overlay = screen.getByTestId("evidence-watermark");
  expect(overlay).toHaveAttribute("aria-hidden", "true");
  expect(overlay).toHaveClass("pointer-events-none", "select-none");
  expect(within(overlay).getAllByText(label)).toHaveLength(12);
}

function restoreDescriptor(
  target: object,
  property: string,
  descriptor: PropertyDescriptor | undefined,
) {
  if (descriptor) {
    Object.defineProperty(target, property, descriptor);
  } else {
    Reflect.deleteProperty(target, property);
  }
}
