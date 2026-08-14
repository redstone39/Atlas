import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";

import { ApiError } from "../../shared/user-messages";
import { notesApi } from "./api";
import { NotesSettingsFeature } from "./NotesSettingsFeature";
import type { NotesSettings } from "./types";

const initialSettings: NotesSettings = {
  checkpoint_interval_seconds: 30,
  settings_revision: 1,
  updated_actor_id: "admin-1",
  updated_at: "2026-08-13T00:00:00Z",
};
const currentSettings: NotesSettings = {
  ...initialSettings,
  checkpoint_interval_seconds: 45,
  settings_revision: 2,
  updated_at: "2026-08-13T00:01:00Z",
};

describe("Notes System Administrator settings", () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
  });

  it("reloads a stale revision and requires the administrator to submit again", async () => {
    vi.spyOn(notesApi, "getSettings")
      .mockResolvedValueOnce(initialSettings)
      .mockResolvedValueOnce(currentSettings);
    vi.spyOn(notesApi, "updateSettings")
      .mockRejectedValueOnce(new ApiError({ error_code: "stale_settings_revision" }, 409))
      .mockResolvedValueOnce({ ...currentSettings, checkpoint_interval_seconds: 60, settings_revision: 3 });

    render(<NotesSettingsFeature />);
    const input = await screen.findByLabelText("Dirty checkpoint interval (seconds)");
    expect(input).toHaveValue(30);
    fireEvent.change(input, { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Notes settings" }));
    expect(await screen.findByText("Settings changed elsewhere")).toBeInTheDocument();

    expect(input).toHaveValue(60);
    expect(screen.getByRole("button", { name: "Save Notes settings" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Save Notes settings" }));
    await waitFor(() => expect(notesApi.updateSettings).toHaveBeenLastCalledWith(currentSettings, 60));
  });
});
