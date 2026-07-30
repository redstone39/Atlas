import { describe, expect, it } from "vitest";

import { documentLibraryProductStatus } from "./document-status";

describe("Document Library product status", () => {
  it.each([
    {
      name: "new active build",
      intakeStatus: "processing",
      evidenceCount: 0,
      processingStatus: "processing",
      expected: "processing",
    },
    {
      name: "active rebuild with a searchable current",
      intakeStatus: "processing",
      evidenceCount: 4,
      processingStatus: "processing",
      expected: "updating",
    },
    {
      name: "ready current",
      intakeStatus: "ready",
      evidenceCount: 4,
      processingStatus: null,
      expected: "searchable",
    },
    {
      name: "failed first build",
      intakeStatus: "failed",
      evidenceCount: 0,
      processingStatus: "failed",
      expected: "failed",
    },
    {
      name: "failed rebuild preserving the old current",
      intakeStatus: "failed",
      evidenceCount: 4,
      processingStatus: "failed",
      expected: "searchable",
    },
  ])("$name maps to $expected", ({
    intakeStatus,
    evidenceCount,
    processingStatus,
    expected,
  }) => {
    expect(documentLibraryProductStatus({
      intakeStatus,
      evidenceCount,
      processingStatus,
    })).toBe(expected);
  });
});
