import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Input } from "./Input";

describe("Input", () => {
  it("associates the label with the field", () => {
    render(<Input label="Email" onChange={() => {}} value="" />);
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
  });

  it("marks the field invalid and announces the error", () => {
    render(<Input label="Email" onChange={() => {}} value="" error="Required" />);
    const field = screen.getByLabelText("Email");
    expect(field).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
  });
});
