import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { useIsMobile } from "../../hooks/use-mobile";
import { workspaceApi } from "../workspace";

vi.mock("../../hooks/use-mobile", () => ({
  useIsMobile: vi.fn(() => false),
}));

vi.mock("./KnowledgeLibraryFeature", () => ({
  KnowledgeLibraryFeature: () => <div>Knowledge detail</div>,
}));

import { KnowledgeScopeFeature } from "./KnowledgeScopeFeature";

describe("Project and Team unified scope directory", () => {
  afterEach(async () => {
    cleanup();
    vi.restoreAllMocks();
    await i18n.changeLanguage("en");
    vi.mocked(useIsMobile).mockReturnValue(false);
  });

  it("renders Workspace tag scopes once and enters the Knowledge route", async () => {
    vi.spyOn(workspaceApi, "workspaceTagScope").mockResolvedValue({
      tags: [
        { tag_type: "team", tag_id: "team-parent", label: "Parent Team" },
        { tag_type: "team", tag_id: "team-child", label: "Child Team" },
        { tag_type: "project", tag_id: "project-other", label: "Other Project" },
      ],
    });
    const onNavigate = vi.fn();

    render(
      <KnowledgeScopeFeature
        scopeType="team"
        scopeId={null}
        workspace
        onNavigate={onNavigate}
      />,
    );

    expect(await screen.findAllByText("Parent Team")).toHaveLength(1);
    expect(screen.getAllByText("Child Team")).toHaveLength(1);
    expect(screen.queryByText("Other Project")).not.toBeInTheDocument();
    expect(screen.queryByText("Available note scopes")).not.toBeInTheDocument();
    expect(workspaceApi.workspaceTagScope).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "Open Parent Team" }));
    expect(onNavigate).toHaveBeenCalledWith("/teams/team-parent/knowledge");
  });

  it("exposes Knowledge and Notes only after the selected scope is authorized", async () => {
    vi.spyOn(workspaceApi, "workspaceTagScope").mockResolvedValue({
      tags: [
        { tag_type: "team", tag_id: "team-parent", label: "Parent Team" },
      ],
    });
    const onNavigate = vi.fn();

    render(
      <KnowledgeScopeFeature
        scopeType="team"
        scopeId="team-parent"
        workspace
        onNavigate={onNavigate}
      />,
    );

    const navigation = await screen.findByRole("navigation", {
      name: "Scope sections",
    });
    expect(
      screen.getByRole("button", { name: "Knowledge" }),
    ).toHaveAttribute("aria-current", "page");
    expect(navigation).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Notes" }));
    expect(onNavigate).toHaveBeenCalledWith(
      "/workspace/teams/team-parent/notes",
    );
  });

  it("renders the same authorized collection as mobile scope cards", async () => {
    vi.mocked(useIsMobile).mockReturnValue(true);
    vi.spyOn(workspaceApi, "workspaceTagScope").mockResolvedValue({
      tags: [
        { tag_type: "project", tag_id: "project-alpha", label: "Project Alpha" },
        { tag_type: "project", tag_id: "project-beta", label: "Project Beta" },
        { tag_type: "team", tag_id: "team-other", label: "Other Team" },
      ],
    });

    render(
      <KnowledgeScopeFeature
        scopeType="project"
        scopeId={null}
        onNavigate={vi.fn()}
      />,
    );

    expect(await screen.findAllByText("Project Alpha")).toHaveLength(1);
    expect(screen.getAllByText("Project Beta")).toHaveLength(1);
    expect(screen.queryByText("Other Team")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open Project Alpha" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Open Project Beta" }),
    ).toBeInTheDocument();
  });
});
