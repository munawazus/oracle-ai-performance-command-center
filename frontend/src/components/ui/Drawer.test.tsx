import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Drawer } from "./Drawer";

describe("Drawer", () => {
  it("renders nothing when closed", () => {
    render(
      <Drawer title="Example" isOpen={false} onClose={vi.fn()}>
        content
      </Drawer>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the title and children when open", () => {
    render(
      <Drawer title="Example" isOpen onClose={vi.fn()}>
        <p>body content</p>
      </Drawer>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Example")).toBeInTheDocument();
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <Drawer title="Example" isOpen onClose={onClose}>
        content
      </Drawer>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Close panel" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed", async () => {
    const onClose = vi.fn();
    render(
      <Drawer title="Example" isOpen onClose={onClose}>
        content
      </Drawer>,
    );

    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when the backdrop is clicked, but not when the panel content is clicked", async () => {
    const onClose = vi.fn();
    render(
      <Drawer title="Example" isOpen onClose={onClose}>
        <button type="button">inside</button>
      </Drawer>,
    );

    await userEvent.click(screen.getByRole("button", { name: "inside" }));
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("dialog").parentElement!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
