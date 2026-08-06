import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerMarkdown } from "./AnswerMarkdown";
import { joinResponseSegmentMarkdown } from "./api";

afterEach(cleanup);

describe("AnswerMarkdown", () => {
  it("renders common Markdown and GFM structures", () => {
    render(
      <AnswerMarkdown
        content={[
          "## Result",
          "",
          "**Important** and `inline code`.",
          "",
          "- First",
          "- Second",
          "",
          "| Name | Value |",
          "| --- | --- |",
          "| Atlas | Ready |",
        ].join("\n")}
      />,
    );

    expect(screen.getByRole("heading", { name: "Result", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("Important").tagName).toBe("STRONG");
    expect(screen.getByText("inline code").tagName).toBe("CODE");
    expect(screen.getByRole("list")).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Ready" })).toBeInTheDocument();
  });

  it("keeps single tildes literal and preserves visible soft line breaks", () => {
    const { container } = render(
      <AnswerMarkdown
        content={[
          "A~B~C",
          "Second paragraph line",
          "",
          "- First list line",
          "  Second list line",
          "",
          "A~~B~~C",
        ].join("\n")}
      />,
    );

    const paragraphs = container.querySelectorAll("p");
    const listItem = screen.getByRole("listitem");
    const listText = listItem.querySelector("span");
    const deletion = container.querySelector("del");

    expect(paragraphs[0]).toHaveTextContent("A~B~C Second paragraph line");
    expect(paragraphs[0]).toHaveClass("whitespace-pre-wrap");
    expect(paragraphs[0].textContent).toContain("\n");
    expect(listItem).toHaveTextContent("First list line Second list line");
    expect(listItem).not.toHaveClass("whitespace-pre-wrap");
    expect(listText).toHaveClass("whitespace-pre-wrap");
    expect(listItem.textContent).toContain("\n");
    expect(deletion).toHaveTextContent("B");
    expect(container.querySelectorAll("del")).toHaveLength(1);
  });

  it("does not preserve Markdown structural whitespace around nested list blocks", () => {
    render(
      <AnswerMarkdown
        content={[
          "- Parent item:",
          "",
          "  - First nested item",
          "",
          "  - Second nested item",
          "",
          "- Next parent item",
        ].join("\n")}
      />,
    );

    const listItems = screen.getAllByRole("listitem");

    expect(listItems).toHaveLength(4);
    for (const listItem of listItems) {
      expect(listItem).not.toHaveClass("whitespace-pre-wrap");
    }
    expect(screen.getByText("Parent item:").closest("p")).toHaveClass("whitespace-pre-wrap");
  });

  it("keeps a following response segment outside the preceding list", () => {
    render(
      <AnswerMarkdown
        content={joinResponseSegmentMarkdown([
          { text: "**First section**\n- List item" },
          { text: "**Second section**\n- Next item" },
        ])}
      />,
    );

    expect(screen.getByText("Second section").closest("li")).toBeNull();
    expect(screen.getAllByRole("list")).toHaveLength(2);
  });

  it("opens only safe external links in a separate browsing context", () => {
    render(
      <AnswerMarkdown
        content="[safe](https://example.com) [unsafe](javascript:alert(1)) [relative](/admin)"
      />,
    );

    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "href",
      "https://example.com",
    );
    expect(screen.getByRole("link", { name: "safe" })).toHaveAttribute(
      "rel",
      "noreferrer noopener",
    );
    expect(screen.getByText("unsafe").closest("a")).toBeNull();
    expect(screen.getByText("relative").closest("a")).toBeNull();
  });

  it("does not execute raw HTML or load model-declared images", () => {
    const { container } = render(
      <AnswerMarkdown
        content={'<script>alert("xss")</script>\n\n![tracking pixel](https://example.com/pixel.png)'}
      />,
    );

    expect(container.querySelector("script")).toBeNull();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("tracking pixel")).toBeInTheDocument();
  });
});
