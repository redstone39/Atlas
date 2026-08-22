import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminSession,
  cleanupAppTest,
  incompleteReadiness,
  memberSession,
  mockApi,
  prepareAppTest,
} from "../App.test-support";
import App from "./atlas-app.test-support";
import { jsonResponse } from "./atlas-app.test-helpers";

const DIGEST_ONE = "1".repeat(64);
const DIGEST_TWO = "2".repeat(64);
const disabledSkill = {
  control: {
    category: "planner" as const,
    name: "compare-options",
    head_revision: 2,
    enabled_revision: null,
    control_revision: 4,
  },
  head: {
    ref: {
      category: "planner" as const,
      name: "compare-options",
      revision: 2,
      content_digest: DIGEST_TWO,
    },
    description: "Compare alternatives before choosing.",
    license: "Apache-2.0",
    compatibility: "Atlas Deep planner",
    metadata: { owner: "planning" },
    created_by: "admin-1",
    created_at: "2026-08-17T10:00:00Z",
    enabled: false,
    source: null,
    instructions: null,
  },
  revisions: [
    {
      ref: {
        category: "planner" as const,
        name: "compare-options",
        revision: 1,
        content_digest: DIGEST_ONE,
      },
      description: "Compare alternatives.",
      license: null,
      compatibility: null,
      metadata: {},
      created_by: "admin-1",
      created_at: "2026-08-16T10:00:00Z",
      enabled: false,
      source: null,
      instructions: null,
    },
    {
      ref: {
        category: "planner" as const,
        name: "compare-options",
        revision: 2,
        content_digest: DIGEST_TWO,
      },
      description: "Compare alternatives before choosing.",
      license: "Apache-2.0",
      compatibility: "Atlas Deep planner",
      metadata: { owner: "planning" },
      created_by: "admin-1",
      created_at: "2026-08-17T10:00:00Z",
      enabled: false,
      source: null,
      instructions: null,
    },
  ],
};

beforeEach(async () => {
  sessionQueryClient.resetSession();
  await prepareAppTest();
  window.history.pushState({}, "", "/admin/prompt-skills");
});
afterEach(cleanupAppTest);
async function openPlanningSlot() {
  expect(
    await screen.findByRole("heading", { name: "Skill slots" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Manage Planning" }));
  expect(
    await screen.findByRole("heading", { name: "Planning Skills" }),
  ).toBeInTheDocument();
}


describe("Atlas production web: Skill slots", () => {
  it("shows three slots and defers category fetch until selection", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    const categories: string[] = [];
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/prompt-skills" &&
        (init?.method ?? "GET") === "GET"
      ) {
        categories.push(url.searchParams.get("category") ?? "");
        return jsonResponse({ items: [] });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    expect(
      await screen.findByText("Understanding"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Planning"),
    ).toBeInTheDocument();
    expect(screen.getByText("Answer")).toBeInTheDocument();
    expect(categories).toEqual([]);

    fireEvent.click(
      screen.getByRole("button", { name: "Manage Understanding" }),
    );
    await waitFor(() => expect(categories).toEqual(["understanding"]));
    fireEvent.click(screen.getByRole("button", { name: "Back to Skill slots" }));
    expect(
      await screen.findByRole("heading", { name: "Skill slots" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Manage Answer" }));
    await waitFor(() =>
      expect(categories).toEqual(["understanding", "answer"]),
    );
  });

  it("shows immutable revisions and reads selected exact revision detail", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/prompt-skills" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ items: [disabledSkill] });
      }
      if (
        url.pathname ===
          "/api/v1/admin/prompt-skills/planner/compare-options/revisions/2" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return jsonResponse({
          ...disabledSkill.head,
          source: "---\nname: compare-options\n---\nUse a decision table.",
          instructions: "Use a decision table.",
        });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    await openPlanningSlot();
    fireEvent.click(screen.getByRole("button", { name: /compare-options/i }));
    expect(await screen.findByText("Revision 1")).toBeInTheDocument();
    expect(screen.getByText("Revision 2")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "View" })[0]);
    expect(
      await screen.findByRole("dialog", { name: "compare-options · Revision 2" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Use a decision table.")).toBeInTheDocument();
    expect(screen.getByText("Apache-2.0")).toBeInTheDocument();
  });

  it("denies non-admin members before loading the catalog", async () => {
    mockApi(memberSession, incompleteReadiness);
    render(<App />);
    expect(
      await screen.findByRole("heading", { name: "Admin access required" }),
    ).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.some(([input]) =>
        String(input).startsWith("/api/v1/admin/prompt-skills"),
      ),
    ).toBe(false);
  });

  it("uploads SKILL.md as a disabled revision with exact head CAS", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let uploadRequest: RequestInit | undefined;
    let listCount = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/prompt-skills" && (init?.method ?? "GET") === "GET") {
        listCount += 1;
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname ===
          "/api/v1/admin/prompt-skills/planner/compare-options/revisions" &&
        init?.method === "POST"
      ) {
        uploadRequest = init;
        return jsonResponse({ skill: disabledSkill, revision: disabledSkill.head, replayed: false }, 201);
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    await openPlanningSlot();
    expect(await screen.findByText("No Skills in this slot")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Upload skill" }));
    fireEvent.change(screen.getByLabelText("Skill name"), {
      target: { value: "compare-options" },
    });
    const file = new File(
      ["---\nname: compare-options\ndescription: Compare options.\n---\nUse a table."],
      "SKILL.md",
      { type: "text/markdown" },
    );
    fireEvent.change(screen.getByLabelText("Skill file"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload revision" }));

    await waitFor(() => expect(uploadRequest).toBeDefined());
    const headers = new Headers(uploadRequest?.headers);
    expect(headers.get("If-Match")).toBe("0");
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(uploadRequest?.body).toBeInstanceOf(FormData);
    expect((uploadRequest?.body as FormData).get("file")).toEqual(file);
    await waitFor(() => expect(listCount).toBe(2));
  });


  it("retries an uncertain new-revision upload with the identical request identity", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    const uploadRequests: RequestInit[] = [];
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/prompt-skills" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ items: [disabledSkill] });
      }
      if (
        url.pathname ===
          "/api/v1/admin/prompt-skills/planner/compare-options/revisions" &&
        init?.method === "POST"
      ) {
        uploadRequests.push(init);
        if (uploadRequests.length === 1) {
          return Promise.reject(new TypeError("connection closed"));
        }
        return jsonResponse({
          skill: disabledSkill,
          revision: disabledSkill.head,
          replayed: true,
        }, 201);
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    await openPlanningSlot();
    fireEvent.click(screen.getByRole("button", { name: "Upload skill" }));
    fireEvent.click(screen.getByLabelText("Upload mode"));
    fireEvent.click(await screen.findByRole("option", { name: "New revision" }));
    fireEvent.click(screen.getByLabelText("Existing skill"));
    fireEvent.click(
      await screen.findByRole("option", { name: "compare-options · Revision 2" }),
    );
    const file = new File(
      ["---\nname: compare-options\ndescription: Compare options.\n---\nUse a new table."],
      "SKILL.md",
      { type: "text/markdown" },
    );
    fireEvent.change(screen.getByLabelText("Skill file"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Upload revision" }));

    const retry = await screen.findByRole("button", {
      name: "Retry identical upload",
    });
    expect(uploadRequests).toHaveLength(1);
    fireEvent.click(retry);
    await waitFor(() => expect(uploadRequests).toHaveLength(2));

    const firstHeaders = new Headers(uploadRequests[0].headers);
    const secondHeaders = new Headers(uploadRequests[1].headers);
    expect(firstHeaders.get("If-Match")).toBe("2");
    expect(secondHeaders.get("If-Match")).toBe("2");
    expect(secondHeaders.get("Idempotency-Key")).toBe(
      firstHeaders.get("Idempotency-Key"),
    );
    expect((uploadRequests[0].body as FormData).get("file")).toEqual(file);
    expect((uploadRequests[1].body as FormData).get("file")).toEqual(file);
  });
  it("sends exact enable intent and reloads stale control state", async () => {
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let listCount = 0;
    let enableRequest: RequestInit | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/prompt-skills" && (init?.method ?? "GET") === "GET") {
        listCount += 1;
        return jsonResponse({ items: [disabledSkill] });
      }
      if (
        url.pathname ===
          "/api/v1/admin/prompt-skills/planner/compare-options/revisions/2/enable" &&
        init?.method === "POST"
      ) {
        enableRequest = init;
        return jsonResponse(
          {
            error_code: "revision_conflict",
            message_code: "prompt_skills.control_revision_changed",
            message_params: {},
          },
          412,
        );
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    await openPlanningSlot();
    fireEvent.click(screen.getByRole("button", { name: /compare-options/i }));
    fireEvent.click(screen.getAllByRole("button", { name: "Enable" })[0]);

    await waitFor(() => expect(enableRequest).toBeDefined());
    const headers = new Headers(enableRequest?.headers);
    expect(headers.get("If-Match")).toBe("4");
    expect(headers.get("Idempotency-Key")).toBeTruthy();
    expect(JSON.parse(String(enableRequest?.body))).toEqual({
      expected_control_revision: 4,
      idempotency_key: headers.get("Idempotency-Key"),
    });
    await waitFor(() => expect(listCount).toBe(2));
    expect(
      await screen.findByText(
        "Another administrator changed this Skill. The latest state was reloaded; review it and try again.",
      ),
    ).toBeInTheDocument();
  });

  it("disables only the currently enabled exact revision", async () => {
    const enabledSkill = {
      ...disabledSkill,
      control: {
        ...disabledSkill.control,
        enabled_revision: 2,
        control_revision: 5,
      },
      revisions: disabledSkill.revisions.map((revision) => ({
        ...revision,
        enabled: revision.ref.revision === 2,
      })),
    };
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    let disableRequest: RequestInit | undefined;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/prompt-skills" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ items: [disableRequest ? disabledSkill : enabledSkill] });
      }
      if (
        url.pathname ===
          "/api/v1/admin/prompt-skills/planner/compare-options/revisions/2/disable" &&
        init?.method === "POST"
      ) {
        disableRequest = init;
        return jsonResponse({
          skill: disabledSkill,
          revision: disabledSkill.head,
          replayed: false,
        });
      }
      return fallbackFetch(input, init);
    });

    render(<App />);
    await openPlanningSlot();
    fireEvent.click(screen.getByRole("button", { name: /compare-options/i }));
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));

    await waitFor(() => expect(disableRequest).toBeDefined());
    const headers = new Headers(disableRequest?.headers);
    expect(headers.get("If-Match")).toBe("5");
    expect(JSON.parse(String(disableRequest?.body))).toEqual({
      expected_control_revision: 5,
      idempotency_key: headers.get("Idempotency-Key"),
    });
    expect(await screen.findAllByText("Disabled")).not.toHaveLength(0);
  });
});
