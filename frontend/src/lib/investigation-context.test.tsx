import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { InvestigationProvider, useInvestigation } from "./investigation-context";

function Probe() {
  const { isOpen, toggle, close, currentDatabaseId, currentDatabaseName, setCurrentDatabase } = useInvestigation();
  return (
    <div>
      <p>isOpen: {String(isOpen)}</p>
      <p>database: {currentDatabaseId ?? "none"} / {currentDatabaseName ?? "none"}</p>
      <button onClick={toggle}>toggle</button>
      <button onClick={close}>close</button>
      <button onClick={() => setCurrentDatabase("db-1", "PROD01")}>select</button>
    </div>
  );
}

describe("useInvestigation", () => {
  it("returns a safe default (no throw) when called with no provider present", () => {
    // This is the whole point of the non-throwing default: DatabaseSelector calls this hook
    // and is rendered inside dozens of existing page tests that don't wrap a provider.
    expect(() => render(<Probe />)).not.toThrow();
    expect(screen.getByText("isOpen: false")).toBeInTheDocument();
    expect(screen.getByText("database: none / none")).toBeInTheDocument();
  });

  it("toggles and closes within a real provider", async () => {
    render(
      <InvestigationProvider>
        <Probe />
      </InvestigationProvider>,
    );

    expect(screen.getByText("isOpen: false")).toBeInTheDocument();
    await userEvent.click(screen.getByText("toggle"));
    expect(screen.getByText("isOpen: true")).toBeInTheDocument();
    await userEvent.click(screen.getByText("close"));
    expect(screen.getByText("isOpen: false")).toBeInTheDocument();
  });

  it("updates the current database within a real provider", async () => {
    render(
      <InvestigationProvider>
        <Probe />
      </InvestigationProvider>,
    );

    await userEvent.click(screen.getByText("select"));
    expect(screen.getByText("database: db-1 / PROD01")).toBeInTheDocument();
  });
});
