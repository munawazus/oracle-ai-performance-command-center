import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge, type Tone } from "./Badge";

const TONES: Tone[] = ["good", "info", "warn", "crit", "critical", "neutral"];

describe("Badge", () => {
  it.each(TONES)("renders the %s tone with a distinct class", (tone) => {
    const { container } = render(<Badge tone={tone}>label</Badge>);
    expect(container.firstElementChild).toHaveAttribute("class", expect.stringContaining(""));
  });

  it("gives CRITICAL a solid fill distinct from HIGH's soft tint", () => {
    const crit = render(<Badge tone="crit">HIGH</Badge>).container.firstElementChild!.className;
    const critical = render(<Badge tone="critical">CRITICAL</Badge>).container.firstElementChild!.className;
    expect(critical).not.toBe(crit);
    expect(critical).toContain("bg-status-crit");
    expect(critical).toContain("text-white");
    expect(crit).toContain("bg-red-50");
  });

  const DARK_CLASS: Record<Tone, string> = {
    good: "dark:bg-green-950/40",
    info: "dark:bg-blue-950/40",
    warn: "dark:bg-amber-950/40",
    crit: "dark:bg-red-950/40",
    critical: "dark:bg-red-500",
    neutral: "dark:bg-slate-800",
  };

  it.each(TONES)("carries a dark: variant for the %s tone", (tone) => {
    const className = render(<Badge tone={tone}>label</Badge>).container.firstElementChild!.className;
    expect(className).toContain(DARK_CLASS[tone]);
  });
});
