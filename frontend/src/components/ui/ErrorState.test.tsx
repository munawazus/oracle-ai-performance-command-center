import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ErrorState } from "./ErrorState";

describe("ErrorState", () => {
  it("renders the message as an alert", () => {
    render(<ErrorState message="Something went wrong." />);

    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong.");
  });

  it("does not render a retry button when onRetry is omitted", () => {
    render(<ErrorState message="Something went wrong." />);

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("calls onRetry when Try again is clicked", async () => {
    const onRetry = vi.fn();
    render(<ErrorState message="Something went wrong." onRetry={onRetry} />);

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
