import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "../../i18n";

import { NoteChangeSetView } from "./NoteChangeSetView";

describe("NoteChangeSetView", () => {
  afterEach(async () => {
    cleanup();
    await i18n.changeLanguage("en");
  });

  it("renders exact document paths and text offsets for every change kind", () => {
    render(<NoteChangeSetView changeSet={{
      moves: [],
      text: [{
        change: "replace",
        path: [0, 1],
        before: "old",
        after: "new",
        from_offset: 2,
        to_offset: 5,
      }],
      nodes: [{
        change: "replace",
        path: [2],
        before_type: "paragraph",
        after_type: "heading",
      }],
      marks: [{
        change: "add",
        path: [3, 0],
        mark_type: "bold",
        before: null,
        after: {},
      }],
      attributes: [{
        path: [4],
        node_type: "heading",
        attribute: "level",
        before: 1,
        after: 2,
      }],
    }} />);

    expect(screen.getByText("Document path: /0/1 · text offsets 2–5")).toBeInTheDocument();
    expect(screen.getByText("Document path: /2")).toBeInTheDocument();
    expect(screen.getByText("Document path: /3/0")).toBeInTheDocument();
    expect(screen.getByText("Document path: /4")).toBeInTheDocument();
  });
});
