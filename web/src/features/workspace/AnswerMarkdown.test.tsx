import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerMarkdown } from "./AnswerMarkdown";

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
