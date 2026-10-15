import { describe, expect, it } from "vitest";

import { SEVERITY_TONE } from "./severity";

describe("SEVERITY_TONE", () => {
  it("maps every IncidentSeverity to a tone", () => {
    for (const severity of ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] as const) {
      expect(SEVERITY_TONE[severity]).toBeDefined();
    }
  });

  it("gives CRITICAL a distinct tone from HIGH - they must not render identically", () => {
    expect(SEVERITY_TONE.CRITICAL).not.toBe(SEVERITY_TONE.HIGH);
  });
});
