import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SearchSelect } from "./SearchSelect";

describe("SearchSelect", () => {
  it("keeps the searchable popover input inside its search row", async () => {
    render(
      <SearchSelect
        value=""
        options={[
          { value: "none", label: "無父團隊" },
          { value: "engineering", label: "Engineering" },
        ]}
        placeholder="父團隊"
        emptyText="沒有符合的團隊"
        onValueChange={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("combobox"));

    const input = await screen.findByPlaceholderText("父團隊");
    const wrapper = input.closest("[data-slot='command-input-wrapper']");

    expect(wrapper).toHaveClass("h-10");
    expect(input).toHaveClass("h-full");
    expect(input).not.toHaveClass("h-10");
  });
});
