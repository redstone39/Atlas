import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  conversationTurnStatusPresentation,
  resultStatusLabel,
  resultStatusSemantic,
  LoadingState,
  StatusBadge,
  TechnicalDetails,
} from "./product-ui";

const translate = (key: string) => key;

describe("shared status semantics", () => {
  it("keeps technical details collapsed behind a native keyboard-focusable button", () => {
    render(
      <TechnicalDetails label="Technical details">
        <span>Internal trace</span>
      </TechnicalDetails>,
    );

    const trigger = screen.getByRole("button", { name: "Technical details" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Internal trace")).toBeNull();

    trigger.focus();
    expect(document.activeElement).toBe(trigger);
    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Internal trace")).not.toBeNull();
  });

  it("renders a concise accessible loading state without explanatory copy", () => {
    render(<LoadingState title="Loading users" />);

    const status = screen.getByRole("status", { name: "Loading users" });
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(screen.getByText("Loading users")).not.toBeNull();
    expect(status.querySelector('[data-slot="empty-description"]')).toBeNull();
  });

  it("keeps completed, progress, clarification, provenance, and unknown states distinct", () => {
    expect(resultStatusLabel("completed", translate)).toBe("status.answered");
    expect(resultStatusSemantic("completed")).toBe("success");
    expect(resultStatusLabel("processing", translate)).toBe("statusValues.processing");
    expect(resultStatusSemantic("processing")).toBe("progress");
    expect(resultStatusLabel("clarification", translate)).toBe("status.clarification");
    expect(resultStatusSemantic("clarification")).toBe("attention");
    expect(resultStatusLabel("external_unverified", translate)).toBe("status.externalUnverified");
    expect(resultStatusSemantic("external_unverified")).toBe("attention");
    expect(resultStatusLabel("mixed_answer", translate)).toBe("status.mixedAnswer");
    expect(resultStatusSemantic("mixed_answer")).toBe("attention");
    expect(resultStatusLabel("verification_incomplete", translate)).toBe("status.verificationIncomplete");
    expect(resultStatusSemantic("verification_incomplete")).toBe("attention");
    expect(resultStatusLabel("unexpected", translate)).toBe("status.unknown");
    expect(resultStatusSemantic("unexpected")).toBe("unknown");
  });

  it("uses one presentation resolver for execution, refusal, validation, and response states", () => {
    const base = {
      execution_status: "completed",
      retryable: false,
      validation_state: "passed",
      response_kind: "grounded_answer",
    };

    expect(conversationTurnStatusPresentation(
      { ...base, execution_status: "failed_closed", retryable: true },
      translate,
    )).toEqual({ label: "status.failedClosed", semantic: "failure" });
    expect(conversationTurnStatusPresentation(
      { ...base, execution_status: "failed_closed", retryable: false },
      translate,
    )).toEqual({ label: "status.refused", semantic: "refused" });
    expect(conversationTurnStatusPresentation(
      { ...base, validation_state: "degraded" },
      translate,
    )).toEqual({ label: "status.verificationIncomplete", semantic: "attention" });
    expect(conversationTurnStatusPresentation(
      { ...base, response_kind: "mixed_answer" },
      translate,
    )).toEqual({ label: "status.mixedAnswer", semantic: "attention" });
  });

  it("renders the icon defined by the shared semantic instead of a page-local icon", () => {
    const { rerender } = render(<StatusBadge semantic="success" label="Answered" />);
    const badge = screen.getByText("Answered").closest('[data-slot="badge"]');
    expect(badge?.getAttribute("data-status-semantic")).toBe("success");
    expect(badge?.querySelector(".lucide-circle-check")).not.toBeNull();
    expect(badge?.querySelector(".lucide-triangle-alert")).toBeNull();

    rerender(<StatusBadge semantic="progress" label="Processing" />);
    expect(screen.getByText("Processing").closest('[data-slot="badge"]')?.getAttribute("data-status-semantic"))
      .toBe("progress");
    expect(screen.getByText("Processing").closest('[data-slot="badge"]')?.querySelector("svg")?.getAttribute("class"))
      .toContain("lucide-clock");

    rerender(<StatusBadge semantic="failure" label="Failed" />);
    expect(screen.getByText("Failed").closest('[data-slot="badge"]')?.querySelector(".lucide-circle-x"))
      .not.toBeNull();
  });
});
