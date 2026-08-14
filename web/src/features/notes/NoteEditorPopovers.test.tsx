import "@testing-library/jest-dom/vitest";

import { Editor } from "@tiptap/core";
import Link from "@tiptap/extension-link";
import { NodeSelection } from "@tiptap/pm/state";
import StarterKit from "@tiptap/starter-kit";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "../../components/ui/tooltip";
import i18n from "../../i18n";
import { NoteImageControls } from "./NoteImageControls";
import { NoteLinkPopover } from "./NoteLinkPopover";
import { noteExtensions } from "./note-extensions";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("note editor popovers", () => {
  it("changes linked display text and destination together", async () => {
    await i18n.changeLanguage("en");
    const editor = new Editor({
      extensions: [StarterKit.configure({ link: false }), Link],
      content: "<p>Hello</p>",
    });
    editor.commands.setTextSelection({ from: 1, to: 6 });

    render(<TooltipProvider><NoteLinkPopover editor={editor} editable /></TooltipProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Link" }));
    fireEvent.change(await screen.findByLabelText("Displayed text"), { target: { value: "Atlas" } });
    fireEvent.change(screen.getByLabelText("URL or email"), { target: { value: "https://atlas.example" } });
    fireEvent.click(screen.getByRole("button", { name: "Save link" }));

    expect(editor.getJSON().content?.[0].content?.[0]).toMatchObject({
      text: "Atlas",
      marks: [{ type: "link", attrs: expect.objectContaining({ href: "https://atlas.example" }) }],
    });
    editor.destroy();
  });

  it("saves alternative text and captions on the selected protected image block", async () => {
    await i18n.changeLanguage("en");
    const editor = new Editor({
      extensions: noteExtensions({ noteId: "note-1", live: false }),
      content: {
        type: "doc",
        content: [{
          type: "noteImage",
          attrs: {
            block_id: "image-1",
            attachment_ref: "attachment-1",
            alt: "",
            caption: "",
            width: 640,
            height: 480,
          },
        }],
      },
    });
    editor.view.dispatch(editor.state.tr.setSelection(NodeSelection.create(editor.state.doc, 0)));

    render(<NoteImageControls editor={editor} noteId="note-1" editable />);
    fireEvent.click(screen.getByRole("button", { name: "Image properties" }));
    fireEvent.change(await screen.findByLabelText("Alternative text"), { target: { value: "Architecture diagram" } });
    fireEvent.change(screen.getByLabelText("Caption"), { target: { value: "Current evidence flow" } });
    fireEvent.click(screen.getByRole("button", { name: "Save properties" }));

    expect(editor.getJSON().content?.[0].attrs).toMatchObject({
      alt: "Architecture diagram",
      caption: "Current evidence flow",
    });
    const clipboardWrite = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { write: clipboardWrite } });
    vi.stubGlobal("ClipboardItem", class {
      constructor(readonly items: Record<string, Blob>) {}
    });
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(
      new Blob([new Uint8Array([1])], { type: "image/png" }),
      { status: 200, headers: { "Content-Type": "image/png" } },
    )));
    vi.stubGlobal("fetch", fetchMock);
    const createObjectUrl = vi.fn(() => "blob:note-image");
    const revokeObjectUrl = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: createObjectUrl });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revokeObjectUrl });
    const anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    fireEvent.click(screen.getByRole("button", { name: "Image properties" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy image" }));
    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Download image" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/notes/note-1/attachments/attachment-1/content",
      { credentials: "same-origin" },
    );
    expect(createObjectUrl).toHaveBeenCalledTimes(1);
    expect(revokeObjectUrl).toHaveBeenCalledWith("blob:note-image");
    editor.destroy();
  });
});
