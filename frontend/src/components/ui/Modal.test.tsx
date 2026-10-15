import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Modal } from "./Modal";

describe("Modal", () => {
  it("renders nothing when closed", () => {
    render(
      <Modal title="Example" isOpen={false} onClose={vi.fn()}>
        content
      </Modal>,
    );

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders the title and children when open", () => {
    render(
      <Modal title="Example" isOpen onClose={vi.fn()}>
        <p>body content</p>
      </Modal>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Example")).toBeInTheDocument();
    expect(screen.getByText("body content")).toBeInTheDocument();
  });

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(
      <Modal title="Example" isOpen onClose={onClose}>
        content
      </Modal>,
    );

    await userEvent.click(screen.getByRole("button", { name: "Close dialog" }));

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Escape is pressed", async () => {
    const onClose = vi.fn();
    render(
      <Modal title="Example" isOpen onClose={onClose}>
        content
      </Modal>,
    );

    await userEvent.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("renders the footer when provided", () => {
    render(
      <Modal title="Example" isOpen onClose={vi.fn()} footer={<button type="button">Save</button>}>
        content
      </Modal>,
    );

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
  });
});
