import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DatabaseSelector } from "./DatabaseSelector";
import { InvestigationProvider, useInvestigation } from "../../lib/investigation-context";
import type { MonitoredDatabase } from "../../types/api";

const DB: MonitoredDatabase = {
  id: "db-1",
  name: "PROD01",
  host: "db.internal",
  port: 1521,
  service_name: "ORCLPDB1",
  edition_hint: "SE2",
  is_active: true,
  tags: [],
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function CurrentDatabaseProbe() {
  const { currentDatabaseId, currentDatabaseName } = useInvestigation();
  return <p>current: {currentDatabaseId ?? "none"} / {currentDatabaseName ?? "none"}</p>;
}

describe("DatabaseSelector", () => {
  it("renders without a provider present, without throwing", () => {
    // Every existing page test renders this component with no InvestigationProvider - this
    // is the guarantee they've all been implicitly relying on, made explicit.
    expect(() =>
      render(<DatabaseSelector databaseId="db-1" onChange={() => {}} databases={[DB]} />),
    ).not.toThrow();
    expect(screen.getByLabelText("Database")).toBeInTheDocument();
  });

  it("reports the selected database into a real InvestigationProvider", async () => {
    render(
      <InvestigationProvider>
        <DatabaseSelector databaseId="db-1" onChange={() => {}} databases={[DB]} />
        <CurrentDatabaseProbe />
      </InvestigationProvider>,
    );

    expect(await screen.findByText("current: db-1 / PROD01")).toBeInTheDocument();
  });

  it("does not report a database until databaseId is set", () => {
    render(
      <InvestigationProvider>
        <DatabaseSelector databaseId="" onChange={() => {}} databases={[DB]} />
        <CurrentDatabaseProbe />
      </InvestigationProvider>,
    );

    expect(screen.getByText("current: none / none")).toBeInTheDocument();
  });
});
