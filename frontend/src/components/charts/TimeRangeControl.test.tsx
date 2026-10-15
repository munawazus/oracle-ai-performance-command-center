import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TimeRangeControl } from "./TimeRangeControl";

describe("TimeRangeControl", () => {
  it("calls onChange with the preset's hours and clears any custom range", async () => {
    const onChange = vi.fn();
    render(<TimeRangeControl value={{ hours: 1 }} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "6h" }));

    expect(onChange).toHaveBeenCalledWith({ hours: 6 });
  });

  it("highlights the active preset", () => {
    render(<TimeRangeControl value={{ hours: 24 }} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "24h" })).toHaveClass("bg-brand-600");
    expect(screen.getByRole("button", { name: "1h" })).not.toHaveClass("bg-brand-600");
  });

  it("shows Custom as active when a custom range is the current value", () => {
    render(<TimeRangeControl value={{ hours: 2, until: "2026-01-01T00:00:00Z" }} onChange={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Custom" })).toHaveClass("bg-brand-600");
  });

  it("opens the custom range form and applies a valid start/end", async () => {
    const onChange = vi.fn();
    render(<TimeRangeControl value={{ hours: 1 }} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "Custom" }));
    await userEvent.type(screen.getByLabelText("Start"), "2026-01-01T00:00");
    await userEvent.type(screen.getByLabelText("End"), "2026-01-01T02:00");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    // `datetime-local` values are interpreted in the runner's own local timezone, so assert
    // against the same conversion rather than a hardcoded UTC string (which would be flaky
    // depending on the machine's offset).
    const expectedUntil = new Date("2026-01-01T02:00").toISOString();
    expect(onChange).toHaveBeenCalledWith({ hours: 2, until: expectedUntil });
  });

  it("rejects an end before start with a visible error, without calling onChange", async () => {
    const onChange = vi.fn();
    render(<TimeRangeControl value={{ hours: 1 }} onChange={onChange} />);

    await userEvent.click(screen.getByRole("button", { name: "Custom" }));
    await userEvent.type(screen.getByLabelText("Start"), "2026-01-02T00:00");
    await userEvent.type(screen.getByLabelText("End"), "2026-01-01T00:00");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(await screen.findByText("End must be after start.")).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});
