import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import {
  adminSession,
  cleanupAppTest,
  incompleteReadiness,
  mockApi,
  prepareAppTest,
} from "../App.test-support";
import App from "./atlas-app.test-support";
import { jsonResponse } from "./atlas-app.test-helpers";

const candidate = {
  candidate_ref: "candidate-public-17",
  draft_key: "draft-public-17",
  disposition: "add" as const,
  category: "planner" as const,
  target_name: "structure-research",
  topic: "research synthesis",
  goal: "Structure evidence before drafting an answer.",
  draft_revision: 3,
  status: "draft" as const,
  skill_source_digest: "7".repeat(64),
  updated_at: "2026-09-03T10:15:00Z",
};

const candidateDetail = {
  ...candidate,
  source_evidence: [
    {
      consolidation_ref: "consolidation-public-4",
      consolidation_digest: "8".repeat(64),
      generalized_experience_ordinal: 2,
    },
  ],
  observed_catalog_refs: [
    {
      category: "planner" as const,
      catalog_revision: 5,
      catalog_digest: "9".repeat(64),
    },
  ],
  matched_skill_refs: [],
  skill_source:
    "---\nname: structure-research\ndescription: Structure research evidence.\n---\nGroup evidence by claim.",
  rationale: "Repeated successful turns used claim-first evidence grouping.",
  risk: "Apply only when multiple sources are available.",
  approved_skill_ref: null,
};

beforeEach(async () => {
  await prepareAppTest();
  window.history.pushState({}, "", "/admin/prompt-skills");
});
afterEach(cleanupAppTest);

describe("Atlas public Admin: Skill candidates", () => {
  it("reviews and approves the exact candidate revision, then refreshes both owners", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    const candidateLists: string[] = [];
    let catalogLoads = 0;
    let approval: RequestInit | undefined;

    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/prompt-skills" && method === "GET") {
        catalogLoads += 1;
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname === "/api/v1/admin/prompt-skill-candidates" &&
        method === "GET"
      ) {
        candidateLists.push(url.searchParams.get("category") ?? "");
        return jsonResponse({ items: [candidate] });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}` &&
        method === "GET"
      ) {
        return jsonResponse(candidateDetail);
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}/approve` &&
        method === "POST"
      ) {
        approval = init;
        return jsonResponse({
          candidate_ref: candidate.candidate_ref,
          draft_revision: candidate.draft_revision,
          status: "approved",
          outcome: "approved",
          approved_skill_ref: {
            category: "planner",
            name: candidate.target_name,
            revision: 1,
            content_digest: candidate.skill_source_digest,
          },
        });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Skill slots" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage Planning" }));

    expect(await screen.findByText(candidate.target_name)).toBeInTheDocument();
    expect(candidateLists).toEqual(["planner"]);
    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(
      await screen.findByText("Repeated successful turns used claim-first evidence grouping."),
    ).toBeInTheDocument();
    expect(screen.getByText("Draft r3")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm" }),
    );

    await waitFor(() => expect(approval).toBeDefined());
    const headers = new Headers(approval?.headers);
    const idempotencyKey = headers.get("Idempotency-Key");
    expect(headers.get("If-Match")).toBe("3");
    expect(idempotencyKey).toBeTruthy();
    expect(JSON.parse(String(approval?.body))).toEqual({
      expected_draft_revision: 3,
      idempotency_key: idempotencyKey,
    });
    await waitFor(() => expect(catalogLoads).toBe(2));
    await waitFor(() => expect(candidateLists).toEqual(["planner", "planner"]));
  });

  it("rejects a draft and reloads it without actionable controls", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let detailLoads = 0;

    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/prompt-skills" && method === "GET") {
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname === "/api/v1/admin/prompt-skill-candidates" &&
        method === "GET"
      ) {
        return jsonResponse({ items: [candidate] });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}` &&
        method === "GET"
      ) {
        detailLoads += 1;
        return jsonResponse({
          ...candidateDetail,
          status: detailLoads === 1 ? "draft" : "rejected",
        });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}/reject` &&
        method === "POST"
      ) {
        return jsonResponse({
          candidate_ref: candidate.candidate_ref,
          draft_revision: candidate.draft_revision,
          status: "rejected",
          outcome: "rejected",
          approved_skill_ref: null,
        });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Skill slots" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage Planning" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm" }),
    );

    await waitFor(() => expect(detailLoads).toBe(2));
    expect(screen.getByText("Rejected")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Reject" }),
    ).not.toBeInTheDocument();
  });

  it("fails closed on a stale revision and reloads before another decision", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let detailLoads = 0;

    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/prompt-skills" && method === "GET") {
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname === "/api/v1/admin/prompt-skill-candidates" &&
        method === "GET"
      ) {
        return jsonResponse({ items: [candidate] });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}` &&
        method === "GET"
      ) {
        detailLoads += 1;
        return jsonResponse({
          ...candidateDetail,
          draft_revision: detailLoads === 1 ? 3 : 4,
        });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}/approve` &&
        method === "POST"
      ) {
        return jsonResponse(
          {
            error_code: "skill_candidate_precondition_changed",
            message_code: "prompt_skills.candidate_precondition_changed",
            message_params: {},
          },
          412,
        );
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Skill slots" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage Planning" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm" }),
    );

    expect(
      await screen.findByText(
        "The candidate or Skill catalog changed. The latest candidate state was reloaded; review it before deciding again.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(detailLoads).toBe(2));
    expect(screen.getByText("Draft r4")).toBeInTheDocument();
    expect(
      screen.queryByText("The exact candidate draft was published and enabled."),
    ).not.toBeInTheDocument();
  });

  it("fails closed on a conflicting idempotency result", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let detailLoads = 0;

    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const method = init?.method ?? "GET";
      if (url.pathname === "/api/v1/admin/prompt-skills" && method === "GET") {
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname === "/api/v1/admin/prompt-skill-candidates" &&
        method === "GET"
      ) {
        return jsonResponse({ items: [candidate] });
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}` &&
        method === "GET"
      ) {
        detailLoads += 1;
        return jsonResponse(candidateDetail);
      }
      if (
        url.pathname ===
          `/api/v1/admin/prompt-skill-candidates/${candidate.candidate_ref}/approve` &&
        method === "POST"
      ) {
        return jsonResponse({
          candidate_ref: candidate.candidate_ref,
          draft_revision: candidate.draft_revision,
          status: "draft",
          outcome: "conflict",
          approved_skill_ref: null,
        });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Skill slots" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage Planning" }));
    fireEvent.click(await screen.findByRole("button", { name: "Review" }));
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Confirm" }),
    );

    expect(
      await screen.findByText(
        "The candidate or Skill catalog changed. The latest candidate state was reloaded; review it before deciding again.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(detailLoads).toBe(2));
    expect(
      screen.queryByText("The exact candidate draft was published and enabled."),
    ).not.toBeInTheDocument();
  });
});
