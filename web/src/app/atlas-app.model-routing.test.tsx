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
import i18n, { LANGUAGE_STORAGE_KEY } from "../i18n";
import { sessionQueryClient } from "../shared/session-query-client";
import {
  adminSession,
  cleanupAppTest,
  incompleteReadiness,
  mockApi,
  prepareAppTest,
} from "../App.test-support";
import {
  chooseDialogOption,
  expectModelRuntimePolicyDraft,
  jsonResponse,
} from "./atlas-app.test-helpers";

beforeEach(() => {
  sessionQueryClient.resetSession();
  prepareAppTest();
});
afterEach(cleanupAppTest);

describe("Atlas production web: model-routing", () => {
it("/admin/models manages provider connections, encrypted-key entry, and models", async () => {
    // acceptance-scenario:SYS-05
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes",
      expect.any(Object),
    );
    expect(await screen.findByText("OpenAI production")).toBeInTheDocument();
    expect(screen.getByText("Migrated provider")).toBeInTheDocument();
    expect(screen.getByText("Manual provider")).toBeInTheDocument();
    expect(screen.getAllByText("API key required").length).toBeGreaterThan(0);
    const connectionsTab = screen.getByRole("tab", { name: "Provider connections" });
    const modelsTab = screen.getByRole("tab", { name: "Models" });
    const answerBehaviorTab = screen.getByRole("tab", { name: "Answer behavior" });
    expect(connectionsTab).toHaveAttribute("aria-selected", "true");
    expect(global.fetch).not.toHaveBeenCalledWith(
      "/api/v1/admin/answer-behavior",
      expect.any(Object),
    );
    expect(screen.queryByText("Primary provider")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /create project/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /upload document/i })).not.toBeInTheDocument();

    fireEvent.mouseDown(answerBehaviorTab, { button: 0 });
    fireEvent.click(answerBehaviorTab);
    const guidance = await screen.findByLabelText("Custom guidance");
    expect(screen.getByText("Current revision 0")).toBeInTheDocument();
    expect(screen.getByText("0 / 2,000 characters")).toBeInTheDocument();
    fireEvent.change(guidance, { target: { value: "😀".repeat(2001) } });
    expect(screen.getByText("2001 / 2,000 characters")).toBeInTheDocument();
    expect(
      screen.getByText("Custom guidance cannot exceed 2,000 characters."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save guidance" })).toBeDisabled();
    fireEvent.change(guidance, {
      target: { value: "Prefer concise comparison tables." },
    });
    expect(screen.getByText("33 / 2,000 characters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save guidance" }));
    expect(await screen.findByText("Current revision 1")).toBeInTheDocument();
    const answerBehaviorPut = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/answer-behavior" &&
        init?.method === "PUT",
    );
    expect(JSON.parse(String(answerBehaviorPut![1]!.body))).toEqual(
      expect.objectContaining({
        custom_guidance: "Prefer concise comparison tables.",
        expected_revision: 0,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Clear guidance" }));
    expect(await screen.findByText("Current revision 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Custom guidance")).toHaveValue("");
    fireEvent.mouseDown(connectionsTab, { button: 0 });
    fireEvent.click(connectionsTab);

    fireEvent.click(screen.getByRole("button", { name: /set api key/i }));
    let dialog = await screen.findByRole("dialog");
    const keyInput = within(dialog).getByLabelText("API key");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(keyInput).toHaveValue("");
    fireEvent.change(keyInput, { target: { value: "rotated-secret-canary" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /save connection/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const connectionPatch = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) ===
          "/api/v1/admin/config/provider-connections/connection-migrated-required" &&
        init?.method === "PATCH",
    );
    expect(connectionPatch).toBeDefined();
    expect(JSON.parse(String(connectionPatch![1]!.body))).toEqual(
      expect.objectContaining({ api_key: "rotated-secret-canary", expected_revision: 1 }),
    );

    fireEvent.mouseDown(modelsTab, { button: 0 });
    fireEvent.click(modelsTab);
    expect(await screen.findByText("Primary provider")).toBeInTheDocument();
    expect(screen.getByText("Secondary provider")).toBeInTheDocument();
    expect(screen.getByText("Text: Primary provider")).toBeInTheDocument();
    expect(
      screen.getByText("Image recognition: Primary provider"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /add connection/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /set api key/i })).not.toBeInTheDocument();
    expect(modelsTab).toHaveAttribute("aria-selected", "true");

    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Models" })).toHaveAttribute(
        "aria-selected",
        "true",
      ),
    );
    expect(screen.getByText("Primary provider")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Advanced settings" }));
    expect(within(dialog).getByText(
      "Copied from the current tested default route: Primary provider. Review it for this model before saving.",
    )).toBeInTheDocument();
    expectModelRuntimePolicyDraft(dialog, {
      "Tokenizer profile": "cl100k_base",
      "Maximum tool executions": 3,
      "Maximum provider invocations": 20,
      "Maximum deep-reasoning revision cycles": 2,
      "Maximum catalog pages": 5,
      "Maximum search rounds": 6,
      "Maximum model-visible items per Turn": 40,
      "Maximum retrieval repairs": 3,
      "Maximum schema retries per turn": 3,
      "Provider invocation timeout": 30,
      "Tool execution timeout": 20,
      "Turn timeout": 90,
      "Context window tokens": 16_000,
      "Maximum input tokens per invocation": 8_000,
      "Maximum output tokens per invocation": 2_000,
      "Maximum tool-result tokens per execution": 4_000,
      "Maximum total tokens per conversation": 20_000,
    });
    await chooseDialogOption(dialog, "Connection", "Manual provider");
    expect(await within(dialog).findByText("Discovery unavailable")).toBeInTheDocument();
    const modelNameInput = within(dialog).getByLabelText("Model or deployment name");
    expect(modelNameInput).toHaveAttribute("role", "combobox");
    expect(modelNameInput).not.toHaveAttribute("list");
    expect(
      within(dialog).getByRole("button", { name: "Show model suggestions" }),
    ).toBeInTheDocument();
    expect(document.querySelector("select, datalist")).toBeNull();
    fireEvent.focus(modelNameInput);
    fireEvent.input(modelNameInput, {
      target: { value: "azure-manual-deployment" },
      inputType: "insertText",
    });
    expect(await screen.findByRole("listbox")).toBeInTheDocument();
    fireEvent.pointerDown(within(dialog).getByLabelText("Route name"));
    fireEvent.focus(within(dialog).getByLabelText("Route name"));
    await waitFor(() =>
      expect(modelNameInput).toHaveValue("azure-manual-deployment"),
    );
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Azure manual deployment route" },
    });
    fireEvent.change(within(dialog).getByLabelText("Maximum output tokens per invocation"), {
      target: { value: "1000" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /save model/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const manualModelCreate = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/config/model-routes" &&
        init?.method === "POST" &&
        String(init.body).includes("azure-manual-deployment"),
    );
    expect(manualModelCreate).toBeDefined();
    expect(JSON.parse(String(manualModelCreate![1]!.body))).toEqual(
      expect.objectContaining({
        display_name: "Azure manual deployment route",
        model_name: "azure-manual-deployment",
        connection_id: "connection-manual-entry",
        runtime_policy: expect.objectContaining({
          tokenizer_profile: "cl100k_base",
          max_output_tokens_per_invocation: 1_000,
        }),
      }),
    );

    let secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(
      within(secondaryRow).getByRole("button", { name: /set as text default/i }),
    ).toBeDisabled();
    expect(
      within(secondaryRow).getByRole("button", {
        name: /set as image recognition default/i,
      }),
    ).toBeDisabled();
    fireEvent.click(within(secondaryRow).getByRole("button", { name: /test route/i }));
    expect((await screen.findAllByText(/passed the controlled test/i)).length).toBeGreaterThan(0);
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    const setTextDefault = within(secondaryRow).getByRole("button", {
      name: /set as text default/i,
    });
    const setVisionDefault = within(secondaryRow).getByRole("button", {
      name: /set as image recognition default/i,
    });
    expect(setTextDefault).toBeEnabled();
    expect(setVisionDefault).toBeEnabled();
    fireEvent.click(setTextDefault);
    expect(await screen.findByText(/Default text model route is updated/i)).toBeInTheDocument();
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(within(secondaryRow).getByText("Text default")).toBeInTheDocument();
    vi.mocked(global.fetch).mockRejectedValueOnce(
      new Error("vision route selection failed"),
    );
    fireEvent.click(
      within(secondaryRow).getByRole("button", {
        name: /set as image recognition default/i,
      }),
    );
    const selectionError = await screen.findByRole("alert");
    expect(selectionError).toHaveTextContent("Action failed");
    expect(selectionError).toHaveTextContent("The request could not be completed");
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    fireEvent.click(
      within(secondaryRow).getByRole("button", {
        name: /set as image recognition default/i,
      }),
    );
    expect(
      await screen.findByText(/Default vision model route is updated/i),
    ).toBeInTheDocument();
    secondaryRow = screen.getByText("Secondary provider").closest("tr")!;
    expect(within(secondaryRow).getByText("Image recognition default")).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes/route-secondary-provider/defaults/text",
      expect.objectContaining({ method: "POST" }),
    );
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes/route-secondary-provider/defaults/vision",
      expect.objectContaining({ method: "POST" }),
    );

    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Discarded model" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /add model/i }));
    dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Advanced settings" }));
    expect(within(dialog).getByLabelText("Route name")).toHaveValue("");
    expect(within(dialog).getByLabelText("Model or deployment name")).toHaveValue("");
    expect(within(dialog).getByText(
      "Copied from the current tested default route: Secondary provider. Review it for this model before saving.",
    )).toBeInTheDocument();
    expectModelRuntimePolicyDraft(dialog, {
      "Tokenizer profile": "o200k_base",
      "Maximum tool executions": 2,
      "Maximum provider invocations": 20,
      "Maximum deep-reasoning revision cycles": 2,
      "Maximum catalog pages": 5,
      "Maximum search rounds": 6,
      "Maximum model-visible items per Turn": 40,
      "Maximum retrieval repairs": 3,
      "Maximum schema retries per turn": 3,
      "Provider invocation timeout": 45,
      "Tool execution timeout": 30,
      "Turn timeout": 120,
      "Context window tokens": 32_000,
      "Maximum input tokens per invocation": 24_000,
      "Maximum output tokens per invocation": 4_000,
      "Maximum tool-result tokens per execution": 8_000,
      "Maximum total tokens per conversation": 48_000,
    });
    fireEvent.change(within(dialog).getByLabelText("Route name"), {
      target: { value: "Production answer provider" },
    });
    const suggestedModelInput = within(dialog).getByLabelText("Model or deployment name");
    fireEvent.input(suggestedModelInput, {
      target: { value: "gpt-4.1" },
      inputType: "insertText",
    });
    const modelSuggestions = await screen.findByRole("listbox");
    fireEvent.click(
      await within(modelSuggestions).findByRole("option", { name: "gpt-4.1-mini" }),
    );
    expect(suggestedModelInput).toHaveValue("gpt-4.1-mini");
    fireEvent.click(within(dialog).getByRole("button", { name: /save model/i }));
    expect((await screen.findAllByText(/Model route is configured/i)).length).toBeGreaterThan(0);
    const modelCreate = vi.mocked(global.fetch).mock.calls.find(
      ([input, init]) =>
        String(input) === "/api/v1/admin/config/model-routes" &&
        init?.method === "POST" &&
        String(init.body).includes("Production answer provider"),
    );
    expect(modelCreate).toBeDefined();
    expect(global.fetch).toHaveBeenCalledWith(
      "/api/v1/admin/config/model-routes",
      expect.objectContaining({
        body: expect.stringMatching(
          /"display_name":"Production answer provider".*"model_name":"gpt-4.1-mini".*"connection_id":"connection-openai-primary"/,
        ),
        method: "POST",
      }),
    );
    expect(String(modelCreate![1]!.body)).not.toContain("secret_ref");
    expect(String(modelCreate![1]!.body)).not.toContain("endpoint_url");
    expect(JSON.parse(String(modelCreate![1]!.body)).runtime_policy).toEqual(
      expect.objectContaining({
        tokenizer_profile: "o200k_base",
        context_window_tokens: 32_000,
        max_total_tokens_per_conversation: 48_000,
      }),
    );
  });

it("/admin/models keeps the original minimal prefills without a tested default route", async () => {
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness, { modelRoutes: [] });
    render(<App />);

    const modelsTab = await screen.findByRole("tab", { name: "Models" });
    fireEvent.mouseDown(modelsTab, { button: 0 });
    fireEvent.click(modelsTab);
    expect(await screen.findByText("No models yet")).toBeInTheDocument();
    expect(screen.getAllByText(/Not assigned/)).toHaveLength(2);
    fireEvent.click(await screen.findByRole("button", { name: /add model/i }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Advanced settings" }));

    expect(
      within(dialog).queryByText(/Copied from the current tested default route:/),
    ).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Tokenizer profile")).toHaveValue("cl100k_base");
    expect(within(dialog).getByLabelText("Maximum tool executions")).toHaveValue(12);
    expect(within(dialog).getByLabelText("Maximum provider invocations")).toHaveValue(26);
    expect(
      within(dialog).getByLabelText("Maximum deep-reasoning revision cycles"),
    ).toHaveValue(2);
    expect(within(dialog).getByLabelText("Maximum catalog pages")).toHaveValue(5);
    expect(within(dialog).getByLabelText("Maximum search rounds")).toHaveValue(6);
    expect(within(dialog).getByLabelText("Maximum model-visible items per Turn")).toHaveValue(40);
    expect(within(dialog).getByLabelText("Maximum retrieval repairs")).toHaveValue(3);
    expect(within(dialog).getByLabelText("Maximum schema retries per turn")).toHaveValue(3);
    expect(within(dialog).getByLabelText("Provider invocation timeout")).toHaveValue(60);
    expect(within(dialog).getByLabelText("Tool execution timeout")).toHaveValue(45);
    expect(within(dialog).getByLabelText("Turn timeout")).toHaveValue(240);
    expect(within(dialog).getByLabelText("Context window tokens")).toHaveValue(400_000);
    expect(within(dialog).getByLabelText("Maximum input tokens per invocation")).toHaveValue(272_000);
    expect(within(dialog).getByLabelText("Maximum output tokens per invocation")).toHaveValue(16_000);
    expect(
      within(dialog).getByLabelText("Maximum tool-result tokens per execution"),
    ).toHaveValue(64_000);
    expect(
      within(dialog).getByLabelText("Maximum total tokens per conversation"),
    ).toHaveValue(1_000_000);
    expect(
      within(dialog).getByRole("button", { name: /save model/i }),
    ).toBeDisabled();
  });

it("/admin/models creates Anthropic and versioned Azure connections", async () => {
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness);
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Models" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    let dialog = await screen.findByRole("dialog");
    await chooseDialogOption(dialog, "Provider type", "Azure OpenAI");
    expect(within(dialog).getByLabelText("Endpoint URL")).toHaveValue(
      "https://example.openai.azure.com",
    );
    const azureVersion = within(dialog).getByLabelText("API version");
    expect(azureVersion).toBeRequired();
    fireEvent.change(within(dialog).getByLabelText("Connection name"), {
      target: { value: "Azure versioned" },
    });
    fireEvent.change(within(dialog).getByLabelText("API key"), {
      target: { value: "azure-secret-canary" },
    });
    expect(within(dialog).getByRole("button", { name: "Save connection" })).toBeDisabled();
    fireEvent.change(azureVersion, { target: { value: "2024-10-21" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    fireEvent.click(await screen.findByRole("button", { name: "Add connection" }));
    dialog = await screen.findByRole("dialog");
    await chooseDialogOption(dialog, "Provider type", "Anthropic");
    expect(within(dialog).queryByLabelText("API version")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Endpoint URL")).toHaveValue(
      "https://api.anthropic.com",
    );
    fireEvent.change(within(dialog).getByLabelText("Connection name"), {
      target: { value: "Anthropic production" },
    });
    fireEvent.change(within(dialog).getByLabelText("API key"), {
      target: { value: "anthropic-secret-canary" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());

    const createBodies = vi.mocked(global.fetch).mock.calls
      .filter(
        ([input, init]) =>
          String(input) === "/api/v1/admin/config/provider-connections" &&
          init?.method === "POST",
      )
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(createBodies).toEqual([
      expect.objectContaining({
        provider_type: "azure_openai",
        endpoint_url: "https://example.openai.azure.com",
        api_version: "2024-10-21",
        api_key: "azure-secret-canary",
      }),
      expect.objectContaining({
        provider_type: "anthropic",
        endpoint_url: "https://api.anthropic.com",
        api_key: "anthropic-secret-canary",
      }),
    ]);
    expect(createBodies[1]).not.toHaveProperty("api_version");
    expect(await screen.findByText("Anthropic production")).toBeInTheDocument();
    const azureCard = (await screen.findByText("Azure versioned")).closest<HTMLElement>(
      '[data-slot="card"]',
    )!;
    expect(within(azureCard).getByText(/API version: 2024-10-21/)).toBeInTheDocument();
  });

it("/admin/models shows the canonical credential encryption error in zh-TW", async () => {
    window.history.pushState({}, "", "/admin/models");
    mockApi(adminSession, incompleteReadiness);
    const fallbackFetch = global.fetch;
    global.fetch = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      if (
        url.pathname === "/api/v1/admin/config/provider-connections" &&
        init?.method === "POST"
      ) {
        return jsonResponse(
          {
            error_code: "credential_master_key_unavailable",
            message_code: "provider.credential_encryption_is_unavailable", message_params: {},
            correlation_id: "corr-p0-local-dev",
            audit_event_ref: null,
          },
          503,
        );
      }
      return fallbackFetch(input, init);
    });
    await i18n.changeLanguage("zh-TW");
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, "zh-TW");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "模型" })).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "新增連線" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("連線名稱"), {
      target: { value: "無法加密的連線" },
    });
    fireEvent.change(within(dialog).getByLabelText("API 金鑰"), {
      target: { value: "secret-canary" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "儲存連線" }));

    expect(
      await screen.findByText("供應商憑證加密服務目前無法使用。"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Request failed.")).not.toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
