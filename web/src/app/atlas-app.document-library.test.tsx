import "@testing-library/jest-dom/vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => import("../test/next-navigation-mock"));

import App from "./atlas-app.test-support";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminWithProjectSession,
  cleanupAppTest,
  mockApi,
  projectAdminSession,
  prepareAppTest,
  readyReadiness,
  teamAdminSession,
  teamUploaderSession,
} from "../App.test-support";
import {
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: document-library", () => {
it("/admin/document-library waits for its scope data before showing settled controls", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/teams" &&
        (init?.method ?? "GET") === "GET"
      ) {
        return new Promise<Response>(() => {});
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading document library" }))
      .toBeInTheDocument();
    expect(screen.queryByLabelText("Target")).not.toBeInTheDocument();
  });

it("/admin/document-library reports scope-source failure and retries its fallback", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    let scopeAttempts = 0;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = new URL(String(input), "http://localhost");
      if (url.pathname === "/api/v1/admin/teams" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
      }
      if (
        url.pathname === "/api/v1/workspace/tag-scope" &&
        (init?.method ?? "GET") === "GET"
      ) {
        scopeAttempts += 1;
        if (scopeAttempts === 1) {
          return jsonResponse({ message_code: "artifact.is_unavailable", message_params: {} }, 503);
        }
      }
      return normalFetch(input, init);
    });

    render(<App />);

    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    expect(screen.queryByLabelText("Target")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    const target = await screen.findByLabelText("Target");
    fireEvent.click(target);
    expect(await screen.findByRole("option", { name: /Team: Platform/ })).toBeInTheDocument();
    expect(scopeAttempts).toBe(2);
  });

it("Document Library uploads selected files sequentially and retries only remaining drafts", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    const uploadCalls: RequestInit[] = [];
    let resolveFirstUpload!: (response: Response) => void;
    let resolveSecondUpload!: (response: Response) => void;
    const firstUpload = new Promise<Response>((resolve) => {
      resolveFirstUpload = resolve;
    });
    const secondUpload = new Promise<Response>((resolve) => {
      resolveSecondUpload = resolve;
    });
    global.fetch = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = new URL(String(input), "http://localhost");
        if (
          url.pathname === "/api/v1/admin/document-library" &&
          init?.method === "POST"
        ) {
          uploadCalls.push(init);
          if (uploadCalls.length === 1) return firstUpload;
          if (uploadCalls.length === 2) return secondUpload;
          return jsonResponse({
            message_code: "document.document_upload_was_accepted",
            message_params: {},
          });
        }
        return normalFetch(input, init);
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByLabelText("Target"));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByRole("option", {
        name: "Team: Signal Integrity",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "Choose one or more documents and add them to the current scope.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    );
    fireEvent.click(
      within(dialog).getByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    );
    const firstFile = new File(["%PDF-1.4"], "first.pdf", {
      type: "application/pdf",
    });
    const secondFile = new File(["word"], "second.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    const fileInput = within(dialog).getByLabelText("Document files");
    expect(fileInput).toHaveAttribute("multiple");
    fireEvent.change(fileInput, {
      target: { files: [firstFile, secondFile] },
    });
    expect(within(dialog).getByText(/first\.pdf/)).toBeInTheDocument();
    expect(within(dialog).getByText(/second\.docx/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Shared batch metadata" },
    });
    fireEvent.click(within(dialog).getByText("Allow member download"));
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload selected files" }),
    );

    expect(await screen.findByText("Uploading 1 of 2…")).toBeInTheDocument();
    expect(uploadCalls).toHaveLength(1);
    expect(within(dialog).getByLabelText("Document files")).toBeDisabled();
    expect(within(dialog).getByLabelText("Document description")).toBeDisabled();
    expect(
      within(dialog).getByRole("checkbox", { name: "Allow member download" }),
    ).toBeDisabled();

    resolveFirstUpload(
      await jsonResponse({
        message_code: "document.document_upload_was_accepted",
        message_params: {},
      }),
    );
    expect(await screen.findByText("Uploading 2 of 2…")).toBeInTheDocument();
    await waitFor(() => expect(uploadCalls).toHaveLength(2));

    const firstForm = uploadCalls[0]!.body as FormData;
    const secondForm = uploadCalls[1]!.body as FormData;
    const firstOperationKey = String(firstForm.get("idempotency_key"));
    const secondOperationKey = String(secondForm.get("idempotency_key"));
    expect(firstForm.get("file")).toBe(firstFile);
    expect(secondForm.get("file")).toBe(secondFile);
    expect(firstForm.has("document_id")).toBe(false);
    expect(secondForm.has("document_id")).toBe(false);
    expect(firstOperationKey).toMatch(/^document-upload-/);
    expect(secondOperationKey).toMatch(/^document-upload-/);
    expect(firstOperationKey).not.toBe(secondOperationKey);
    for (const form of [firstForm, secondForm]) {
      expect(form.get("scope_type")).toBe("team");
      expect(form.get("scope_id")).toBe("team-si");
      expect(JSON.parse(String(form.get("tag_refs")))).toEqual([
        { tag_type: "team", tag_id: "team-si" },
        { tag_type: "project", tag_id: "proj-admin-live" },
      ]);
      expect(form.get("description")).toBe("Shared batch metadata");
      expect(form.get("allow_member_download")).toBe("true");
    }

    resolveSecondUpload(
      await jsonResponse(
        {
          message_code: "artifact.storage_is_temporarily_unavailable",
          message_params: {},
        },
        503,
      ),
    );
    await waitFor(() =>
      expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(
        "Storage is temporarily unavailable. Try again later.",
      ),
    );
    const retryDialog = screen.getByRole("dialog");
    expect(within(retryDialog).queryByText(/first\.pdf/)).not.toBeInTheDocument();
    expect(within(retryDialog).getByText(/second\.docx/)).toBeInTheDocument();
    expect(within(retryDialog).getByLabelText("Document files")).toHaveProperty(
      "files.length",
      0,
    );

    fireEvent.click(
      within(retryDialog).getByRole("button", { name: "Upload selected files" }),
    );
    await waitFor(() => expect(uploadCalls).toHaveLength(3));
    const retryForm = uploadCalls[2]!.body as FormData;
    expect(retryForm.get("file")).toBe(secondFile);
    expect(retryForm.has("document_id")).toBe(false);
    expect(retryForm.get("idempotency_key")).toBe(secondOperationKey);
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
  });

it("Document Library upload preserves draft on failure", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    let uploadAttempts = 0;
    let resolveFirstUpload!: (response: Response) => void;
    const firstUpload = new Promise<Response>((resolve) => {
      resolveFirstUpload = resolve;
    });
    global.fetch = vi.fn(
      (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = new URL(String(input), "http://localhost");
        if (
          url.pathname === "/api/v1/admin/document-library" &&
          init?.method === "POST"
        ) {
          uploadAttempts += 1;
          if (uploadAttempts === 1) return firstUpload;
        }
        return normalFetch(input, init);
      },
    );
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Document Library" }),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByLabelText("Target"));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByRole("option", {
        name: "Team: Signal Integrity",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(
        "Supports PDF, Word, PowerPoint, Excel, TXT, and CSV; select one or more files, each up to 250 MiB.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    );
    fireEvent.click(
      within(dialog).getByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    );
    const uploadFile = new File(["%PDF-1.4"], "retry-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("Document files"), {
      target: { files: [uploadFile] },
    });
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Preserve this draft" },
    });
    fireEvent.click(within(dialog).getByText("Allow member download"));
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload selected files" }),
    );
    const uploadingLabel = await screen.findByText("Uploading 1 of 1…");
    expect(uploadingLabel.closest("button")).toBeDisabled();
    const pendingDialog = uploadingLabel.closest('[role="dialog"]') as HTMLElement;
    expect(
      within(pendingDialog).getByRole("button", { name: "Cancel" }),
    ).toBeDisabled();
    expect(
      within(pendingDialog).queryByRole("button", { name: "Close" }),
    ).not.toBeInTheDocument();
    fireEvent.keyDown(pendingDialog, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBe(pendingDialog);
    const overlay = document.querySelector('[data-slot="dialog-overlay"]');
    expect(overlay).not.toBeNull();
    fireEvent.pointerDown(overlay!);
    fireEvent.click(overlay!);
    expect(screen.getByRole("dialog")).toBe(pendingDialog);
    resolveFirstUpload(
      await jsonResponse(
        {
          message_code: "artifact.storage_is_temporarily_unavailable",
          message_params: {},
        },
        503,
      ),
    );

    await waitFor(() =>
      expect(
        within(screen.getByRole("dialog")).getByRole("alert"),
      ).toHaveTextContent(
        "Storage is temporarily unavailable. Try again later.",
      ),
    );
    const retryDialog = screen.getByRole("dialog");
    expect(within(retryDialog).getByLabelText("Document files")).toHaveProperty(
      "files.0",
      uploadFile,
    );
    expect(within(retryDialog).getByLabelText("Document description")).toHaveValue(
      "Preserve this draft",
    );
    expect(
      within(retryDialog).getByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    ).toBeChecked();
    expect(
      within(retryDialog).getByRole("checkbox", {
        name: "Allow member download",
      }),
    ).toBeChecked();

    fireEvent.click(
      within(retryDialog).getByRole("button", { name: "Upload selected files" }),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(uploadAttempts).toBe(2);
  });

it("Document Library upload closes after acceptance before refresh failure", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    const normalFetch = global.fetch;
    let uploadAccepted = false;
    global.fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
        const url = new URL(String(input), "http://localhost");
        if (
          url.pathname === "/api/v1/admin/document-library" &&
          init?.method === "POST"
        ) {
          uploadAccepted = true;
          return normalFetch(input, init);
        }
        if (
          uploadAccepted &&
          url.pathname === "/api/v1/admin/document-library" &&
          (init?.method ?? "GET") === "GET"
        ) {
          return jsonResponse(
            {
              message_code: "artifact.storage_is_temporarily_unavailable",
              message_params: {},
            },
            503,
          );
        }
        return normalFetch(input, init);
      },
    );
    render(<App />);

    fireEvent.click(await screen.findByLabelText("Target"));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByRole("option", {
        name: "Project: Admin Live Project",
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    const dialog = await screen.findByRole("dialog");
    const uploadFile = new File(["%PDF-1.4"], "accepted-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("Document files"), {
      target: { files: [uploadFile] },
    });
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Upload selected files" }),
    );

    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(await screen.findByText("Could not load this list")).toBeInTheDocument();
    expect(
      vi.mocked(global.fetch).mock.calls.filter(
        ([input, init]) =>
          new URL(String(input), "http://localhost").pathname ===
            "/api/v1/admin/document-library" && init?.method === "POST",
      ),
    ).toHaveLength(1);
  });

it("Document Library requires one target before upload", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Document Library" }),
    ).toBeInTheDocument();
    const uploadButton = await screen.findByRole("button", {
      name: "Upload document",
    });
    expect(uploadButton).toBeDisabled();
    expect(
      screen.getByText("Select one Team or Project before uploading."),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Target"));
    fireEvent.click(
      within(await screen.findByRole("listbox")).getByRole("option", {
        name: "Project: Admin Live Project",
      }),
    );
    expect(uploadButton).toBeEnabled();
    expect(
      screen.queryByText("Select one Team or Project before uploading."),
    ).not.toBeInTheDocument();
  });

it("Team uploader cannot open Team management directly", async () => {
    window.history.pushState({}, "", "/admin/teams");
    mockApi(teamUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Admin access required" })).toBeInTheDocument();
    const productNavigation = screen.getByRole("navigation", { name: "Product" });
    expect(within(productNavigation).getByRole("button", { name: "Teams" }))
      .toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Management" }))
      .not.toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith("/api/v1/admin/teams", expect.any(Object));
  });

it("Document Library feature preserves upload update download lifecycle and event interactions", async () => {
    // acceptance-scenario:SYS-07
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(adminWithProjectSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(await screen.findByText("Uploader-owned Team note")).toBeInTheDocument();
    const updatingRow = screen.getByText("Uploader-owned Team note").closest("tr");
    const processingRow = screen.getByText("Uploader-owned Project note").closest("tr");
    expect(updatingRow).not.toBeNull();
    expect(processingRow).not.toBeNull();
    expect(within(updatingRow!).getByText("Updating")).toBeInTheDocument();
    expect(within(updatingRow!).getByText("Active")).toBeInTheDocument();
    expect(within(updatingRow!).queryByText("Parsing")).not.toBeInTheDocument();
    expect(within(processingRow!).getByText("Processing")).toBeInTheDocument();
    let disabledRow = screen.getByText("Disabled Team note").closest("tr");
    expect(disabledRow).not.toBeNull();
    expect(within(disabledRow!).getByText("Searchable")).toBeInTheDocument();
    expect(within(disabledRow!).getByText("Disabled")).toBeInTheDocument();
    expect(within(disabledRow!).queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Target"));
    const listbox = await screen.findByRole("listbox");
    fireEvent.click(within(listbox).getByRole("option", { name: "Team: Signal Integrity" }));
    fireEvent.click(screen.getByRole("button", { name: "Upload document" }));
    let dialog = await screen.findByRole("dialog");
    const uploadFile = new File(["%PDF-1.4"], "feature-upload.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(within(dialog).getByLabelText("Document files"), {
      target: { files: [uploadFile] },
    });
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Feature-owned upload" },
    });
    fireEvent.click(
      within(dialog).getByRole("button", {
        name: "Add other Teams or Projects (optional)",
      }),
    );
    fireEvent.click(
      within(dialog).getByRole("checkbox", {
        name: "Project: Admin Live Project",
      }),
    );
    fireEvent.click(within(dialog).getByText("Allow member download"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Upload selected files" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library",
        expect.objectContaining({ method: "POST", body: expect.any(FormData) }),
      ),
    );
    const uploadCall = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/document-library" && init?.method === "POST",
    );
    expect(uploadCall).toBeDefined();
    const uploadForm = uploadCall![1]!.body as FormData;
    expect(uploadForm.get("scope_type")).toBe("team");
    expect(uploadForm.get("scope_id")).toBe("team-si");
    expect(JSON.parse(String(uploadForm.get("tag_refs")))).toEqual([
      { tag_type: "team", tag_id: "team-si" },
      { tag_type: "project", tag_id: "proj-admin-live" },
    ]);
    expect(uploadForm.get("description")).toBe("Feature-owned upload");
    expect(uploadForm.get("allow_member_download")).toBe("true");
    expect(uploadForm.get("file")).toBe(uploadFile);

    fireEvent.click((await screen.findAllByRole("button", { name: "Download" }))[0]);
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/library/documents/doc-team-uploader-owned/content",
        { credentials: "include", method: "HEAD" },
      ),
    );

    fireEvent.click(screen.getAllByRole("button", { name: "Manage" })[0]);
    dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Office preview unavailable/)).toBeInTheDocument();
    expect(within(dialog).getByText(/default-office r1/)).toBeInTheDocument();
    expect(within(dialog).getByText("3 / 10 pages")).toBeInTheDocument();
    expect(within(dialog).getByText("0:45")).toBeInTheDocument();
    expect(await within(dialog).findByText("Document is uploaded.")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/document-library/doc-team-uploader-owned/events",
      expect.any(Object),
    );
    fireEvent.change(within(dialog).getByLabelText("Document description"), {
      target: { value: "Updated by feature" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save description" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-uploader-owned",
        expect.objectContaining({ method: "PATCH" }),
      ),
    );
    const descriptionPatch = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/document-library/doc-team-uploader-owned" &&
        init?.method === "PATCH" &&
        JSON.parse(String(init.body)).description === "Updated by feature",
    );
    expect(descriptionPatch).toBeDefined();

    fireEvent.click(within(dialog).getByText("Allow member download"));
    await waitFor(() =>
      expect(
        vi.mocked(global.fetch).mock.calls.some(
          ([input, init]) =>
            String(input) === "/api/v1/admin/document-library/doc-team-uploader-owned" &&
            init?.method === "PATCH" &&
            JSON.parse(String(init.body)).allow_member_download === true,
        ),
      ).toBe(true),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: /^stop$/i }));
    const stopDialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(stopDialog).getByRole("button", { name: /^stop$/i }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/processing/jobs/job-team-uploader-owned/cancel",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const retryProcessing = await within(dialog).findByRole("button", { name: /^retry$/i });
    fireEvent.click(retryProcessing);
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/processing/jobs/job-team-uploader-owned/retry",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/document-library/doc-team-uploader-owned/refresh-searchable-content",
      expect.any(Object),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Disable" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-uploader-owned/disable",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    disabledRow = screen.getByText("Disabled Team note").closest("tr");
    expect(disabledRow).not.toBeNull();
    fireEvent.click(within(disabledRow!).getByRole("button", { name: "Manage" }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
    await waitFor(() =>
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/v1/admin/document-library/doc-team-disabled/restore",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

it.each([
    {
      label: "Team Admin",
      session: teamAdminSession,
      allowedTitle: "Uploader-owned Team note",
      allowedDocumentId: "doc-team-uploader-owned",
      deniedTitle: null,
      excludedTitle: "Uploader-owned Project note",
      deniedDocumentId: "doc-project-uploader-owned",
    },
    {
      label: "Project Admin",
      session: projectAdminSession,
      allowedTitle: "Uploader-owned Project note",
      allowedDocumentId: "doc-project-uploader-owned",
      deniedTitle: "Uploader-owned Team note",
      excludedTitle: null,
      deniedDocumentId: "doc-team-uploader-owned",
    },
  ])(
    "scope admins download owner-scoped documents when member download is disabled: $label",
    async ({
      session,
      allowedTitle,
      allowedDocumentId,
      deniedTitle,
      deniedDocumentId,
      excludedTitle,
    }) => {
      window.history.pushState({}, "", "/admin/document-library");
      mockApi(session, readyReadiness);
      render(<App />);

      expect(
        await screen.findByRole("heading", { name: "Document Library" }),
      ).toBeInTheDocument();
      const allowedRow = (await screen.findByText(allowedTitle)).closest("tr");
      expect(allowedRow).not.toBeNull();
      const download = within(allowedRow!).getByRole("button", {
        name: "Download",
      });
      if (excludedTitle) {
        expect(screen.queryByText(excludedTitle)).not.toBeInTheDocument();
      }
      if (deniedTitle) {
        const deniedRow = screen.getByText(deniedTitle).closest("tr");
        expect(deniedRow).not.toBeNull();
        expect(
          within(deniedRow!).queryByRole("button", { name: "Download" }),
        ).not.toBeInTheDocument();
      }

      fireEvent.click(download);
      expect(
        screen.getAllByRole("button", { name: "Download" }),
      ).toHaveLength(1);

      await waitFor(() =>
        expect(global.fetch).toHaveBeenCalledWith(
          `/api/v1/library/documents/${allowedDocumentId}/content`,
          { credentials: "include", method: "HEAD" },
        ),
      );
      expect(global.fetch).not.toHaveBeenCalledWith(
        `/api/v1/library/documents/${deniedDocumentId}/content`,
        { credentials: "include", method: "HEAD" },
      );
    },
  );

it("Team uploader Document Library hides non-executable policy and lifecycle controls", async () => {
    window.history.pushState({}, "", "/admin/document-library");
    mockApi(teamUploaderSession, readyReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Document Library" })).toBeInTheDocument();
    expect(await screen.findByText("Uploader-owned Team note")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Uploader-owned Project note")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Target")).toHaveTextContent("Signal Integrity");
    expect(screen.queryByText("Platform")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Manage" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Save description" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Update searchable content" })).toBeInTheDocument();
    expect(within(dialog).queryByText("Allow member download")).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Disable" })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Restore" })).not.toBeInTheDocument();
  });
});
