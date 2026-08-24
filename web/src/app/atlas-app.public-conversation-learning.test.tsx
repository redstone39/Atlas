import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import i18n from "../i18n";
import { conversationReviewApi } from "../features/conversation-review/api";
import { ConversationLearningSettingsFeature } from "../features/conversation-review/ConversationLearningSettingsFeature";
import type { ConversationLearningSettings } from "../features/conversation-review/types";
import { ApiError } from "../shared/user-messages";

const baseline: ConversationLearningSettings = {
  enabled: true,
  settings_revision: 1,
  updated_actor_id: "actor-public-synthetic-admin",
  updated_at: "2026-08-24T00:00:00Z",
};

const concurrent: ConversationLearningSettings = {
  ...baseline,
  enabled: false,
  settings_revision: 2,
  updated_at: "2026-08-24T00:01:00Z",
};

describe("public conversation learning settings", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
  });

  it("preserves the administrator draft after a stale revision refresh", async () => {
    vi.spyOn(conversationReviewApi, "getLearningSettings")
      .mockResolvedValueOnce(baseline)
      .mockResolvedValueOnce(concurrent);
    vi.spyOn(conversationReviewApi, "updateLearningSettings")
      .mockRejectedValueOnce(new ApiError({ error_code: "stale_settings_revision" }, 409))
      .mockResolvedValueOnce({ ...concurrent, enabled: true, settings_revision: 3 });

    render(<ConversationLearningSettingsFeature />);
    const toggle = await screen.findByRole("switch", {
      name: "Allow new conversation learning cycles",
    });
    expect(toggle).toBeChecked();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save conversation learning setting" }));
    expect(await screen.findByText("Setting changed elsewhere")).toBeInTheDocument();
    expect(toggle).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Save conversation learning setting" })).toBeDisabled();

    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: "Save conversation learning setting" }));
    await waitFor(() =>
      expect(conversationReviewApi.updateLearningSettings).toHaveBeenLastCalledWith(
        concurrent,
        true,
      ),
    );
  });

  it("shows a retry path when the canonical setting cannot be read", async () => {
    vi.spyOn(conversationReviewApi, "getLearningSettings")
      .mockRejectedValueOnce(new Error("public-synthetic read failure"))
      .mockResolvedValueOnce(baseline);

    render(<ConversationLearningSettingsFeature />);
    expect(await screen.findByText("Unable to load conversation learning settings")).toBeInTheDocument();
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("switch", {
      name: "Allow new conversation learning cycles",
    })).toBeChecked();
  });
});
