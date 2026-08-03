import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import i18n from "../../i18n";
import { AnswerEvidenceSummary } from "./AnswerEvidenceSummary";
import { ClaimedEvidenceTrace } from "./ClaimedEvidenceTrace";
import { MessageSources } from "./WorkspaceFeature";
import { ReasoningTimeline } from "./ReasoningTimeline";

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

describe("workspace answer evidence presentation", () => {
  const reasoningProgress = [{
    event_id: "reasoning-1",
    sequence: 1,
    phase: "planning" as const,
    status: "completed" as const,
    cycle: null,
    message_code: "reasoning.planning_completed",
    message_params: { plan_items: 2 },
    created_at: "2026-08-01T00:00:00Z",
  }];

  it("shows live safe reasoning progress and collapses completed timelines", () => {
    const { rerender } = render(
      <ReasoningTimeline items={reasoningProgress} live />,
    );
    expect(screen.getByText("Planning the work")).toBeInTheDocument();
    expect(screen.queryByText(/score/i)).not.toBeInTheDocument();

    rerender(<ReasoningTimeline items={reasoningProgress} />);
    expect(screen.queryByText("Planning the work")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Process" }));
    expect(screen.getByText("Planning the work")).toBeInTheDocument();
  });

  it("only shows a source warning when the answer needs review", () => {
    const { rerender } = render(
      <AnswerEvidenceSummary status="evidence_aligned" items={[]} onOpen={vi.fn()} />,
    );
    expect(screen.queryByText("Sources aligned")).not.toBeInTheDocument();

    rerender(<AnswerEvidenceSummary status="questionable" items={[]} onOpen={vi.fn()} />);
    const warning = screen.getByText("Check sources");
    expect(warning.closest('[data-slot="badge"]')).toHaveAttribute(
      "data-status-semantic",
      "attention",
    );
  });

  it("uses the concise Traditional Chinese source warning", async () => {
    await i18n.changeLanguage("zh-TW");
    const { rerender } = render(
      <AnswerEvidenceSummary status="evidence_aligned" items={[]} onOpen={vi.fn()} />,
    );
    expect(screen.queryByText("來源一致")).not.toBeInTheDocument();

    rerender(<AnswerEvidenceSummary status="questionable" items={[]} onOpen={vi.fn()} />);
    expect(screen.getByText("請確認來源")).toBeInTheDocument();
  });

  it("renders no answer-level status before a terminal projection supplies one", () => {
    const { container } = render(
      <AnswerEvidenceSummary status={null} items={[]} onOpen={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows deduplicated document and page labels without technical trace details", () => {
    const onOpen = vi.fn();
    render(
      <AnswerEvidenceSummary
        status="questionable"
        onOpen={onOpen}
        items={[
          {
            position: 1,
            handle: "kh_evidence_a",
            resolution_status: "resolved",
            duplicate_of_position: null,
            handle_kind: "evidence",
            evidence_ref: "evidence-a",
            result_ref: "result-a",
            invocation_ordinal: 2,
            document_ref: "document-a",
            document_handle: "kh_document_a",
            lifecycle_epoch: 1,
            document_version_ref: "document-version-a",
            processing_revision_ref: "processing-revision-a",
            processing_generation_ref: "processing-generation-a",
            index_generation_ref: "index-generation-a",
            document_display_name: "Policy.pdf",
            document_version_label: "v1",
            page_number: 2,
            locator_label: "Page 2",
            review_resolution_reason: "resolved",
            protected_open_ref: "declared-evidence-open-a",
          },
          {
            position: 2,
            handle: "kh_evidence_a_duplicate",
            resolution_status: "resolved",
            duplicate_of_position: 1,
            handle_kind: "evidence",
            evidence_ref: "evidence-a-duplicate",
            result_ref: "result-a-duplicate",
            invocation_ordinal: 3,
            document_ref: "document-a",
            document_handle: "kh_document_a",
            lifecycle_epoch: 1,
            document_version_ref: "document-version-a",
            processing_revision_ref: "processing-revision-a",
            processing_generation_ref: "processing-generation-a",
            index_generation_ref: "index-generation-a",
            document_display_name: "Policy.pdf",
            document_version_label: "v1",
            page_number: 2,
            locator_label: "Page 2",
            review_resolution_reason: "resolved",
            protected_open_ref: "declared-evidence-open-a-duplicate",
          },
          {
            position: 3,
            handle: "unknown-handle",
            resolution_status: "unresolved",
            duplicate_of_position: null,
            handle_kind: null,
            evidence_ref: null,
            result_ref: null,
            invocation_ordinal: null,
            document_ref: null,
            document_handle: null,
            lifecycle_epoch: null,
            document_version_ref: null,
            processing_revision_ref: null,
            processing_generation_ref: null,
            index_generation_ref: null,
            document_display_name: null,
            document_version_label: null,
            page_number: null,
            locator_label: null,
            review_resolution_reason: "unknown_or_out_of_execution",
            protected_open_ref: null,
          },
        ]}
      />,
    );

    expect(screen.getAllByText("Policy.pdf · Page 2")).toHaveLength(1);
    expect(screen.queryByText("kh_evidence_a")).not.toBeInTheDocument();
    expect(screen.queryByText("evidence-a")).not.toBeInTheDocument();
    expect(screen.queryByText("unknown-handle")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Open cited document Policy.pdf · Page 2",
      }),
    );
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith("declared-evidence-open-a");
  });

  it("deduplicates verified citation refs without changing the click reference", () => {
    const onOpen = vi.fn();
    const citation = {
      citation_id: "citation-a",
      document_title: "citation-a",
      locator_label: "Protected citation reference",
      snippet: "",
      viewer_available: true,
    };
    render(<MessageSources citations={[citation, citation]} onOpen={onOpen} />);

    const sources = screen.getAllByRole("button", { name: /Open evidence/i });
    expect(sources).toHaveLength(1);
    fireEvent.click(sources[0]);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith("citation-a");
  });

  it("opens only resolved declared evidence with a protected open ref", () => {
    const onOpen = vi.fn();
    render(
      <ClaimedEvidenceTrace
        onOpen={onOpen}
        items={[
          {
            position: 1,
            handle: "kh_evidence_a",
            resolution_status: "resolved",
            duplicate_of_position: null,
            handle_kind: "evidence",
            evidence_ref: "evidence-a",
            result_ref: "result-a",
            invocation_ordinal: 2,
            document_ref: "document-a",
            document_handle: "kh_document_a",
            lifecycle_epoch: 1,
            document_version_ref: "document-version-a",
            processing_revision_ref: "processing-revision-a",
            processing_generation_ref: "processing-generation-a",
            index_generation_ref: "index-generation-a",
            document_display_name: "Policy.pdf",
            document_version_label: "v1",
            page_number: 2,
            locator_label: "Page 2",
            review_resolution_reason: "resolved",
            protected_open_ref: "declared-evidence-open-a",
          },
          {
            position: 2,
            handle: "unknown-handle",
            resolution_status: "unresolved",
            duplicate_of_position: null,
            handle_kind: null,
            evidence_ref: null,
            result_ref: null,
            invocation_ordinal: null,
            document_ref: null,
            document_handle: null,
            lifecycle_epoch: null,
            document_version_ref: null,
            processing_revision_ref: null,
            processing_generation_ref: null,
            index_generation_ref: null,
            document_display_name: null,
            document_version_label: null,
            page_number: null,
            locator_label: null,
            review_resolution_reason: "unknown_or_out_of_execution",
            protected_open_ref: null,
          },
        ]}
      />,
    );

    expect(screen.getByText("Model-declared evidence")).toBeInTheDocument();
    expect(screen.getByText("kh_evidence_a")).toBeInTheDocument();
    expect(screen.getByText("evidence-a")).toBeInTheDocument();
    expect(screen.getByText("unknown-handle")).toBeInTheDocument();
    const open = screen.getByRole("button", { name: "Open declared evidence" });
    fireEvent.click(open);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onOpen).toHaveBeenCalledWith("declared-evidence-open-a");
  });

  it("keeps access-required declared evidence nonclickable and hides lineage", () => {
    render(
      <ClaimedEvidenceTrace
        onOpen={vi.fn()}
        items={[
          {
            position: 1,
            handle: "kh_evidence_revoked",
            resolution_status: "access_required",
            duplicate_of_position: null,
            handle_kind: null,
            evidence_ref: null,
            result_ref: null,
            invocation_ordinal: null,
            document_ref: null,
            document_handle: null,
            lifecycle_epoch: null,
            document_version_ref: null,
            processing_revision_ref: null,
            processing_generation_ref: null,
            index_generation_ref: null,
            document_display_name: null,
            document_version_label: null,
            page_number: null,
            locator_label: null,
            review_resolution_reason: null,
            protected_open_ref: null,
          },
        ]}
      />,
    );

    expect(screen.getByText("Access required")).toBeInTheDocument();
    expect(screen.getByText("kh_evidence_revoked")).toBeInTheDocument();
    expect(screen.queryByText("Document ref")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("states that a completed answer declared no evidence handles", () => {
    render(<ClaimedEvidenceTrace items={[]} showEmpty />);

    expect(
      screen.getByText("The model reported no evidence handles for this answer."),
    ).toBeInTheDocument();
  });
});
