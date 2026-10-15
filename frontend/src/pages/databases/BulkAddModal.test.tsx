import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BulkAddModal } from "./BulkAddModal";
import type { BulkCreateDatabasesResponse } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderModal(onDone = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <BulkAddModal isOpen onClose={vi.fn()} onDone={onDone} />
    </QueryClientProvider>,
  );
}

const ROW = {
  name: "BULK01",
  host: "h",
  port: 1521,
  service_name: "s",
  edition_hint: "SE2",
  username: "u",
  password: "p",
};

describe("BulkAddModal", () => {
  it("rejects invalid JSON before submitting", async () => {
    renderModal();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: "not json" } });
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    expect(await screen.findByText("Not valid JSON.")).toBeInTheDocument();
    expect(mockApiFetch).not.toHaveBeenCalled();
  });

  it("rejects a non-array payload", async () => {
    renderModal();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: '{"not": "an array"}' } });
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    expect(await screen.findByText("Must be a non-empty JSON array of database objects.")).toBeInTheDocument();
  });

  it("submits a valid batch and shows per-row results", async () => {
    const response: BulkCreateDatabasesResponse = {
      created_count: 1,
      failed_count: 1,
      results: [
        { index: 0, status: "created", database: { ...ROW, id: "db-1", is_active: true, tags: [], created_at: "", updated_at: "" } as never, error: null },
        { index: 1, status: "failed", database: null, error: "A database with this name is already registered." },
      ],
    };
    mockApiFetch.mockResolvedValueOnce(response);
    renderModal();

    fireEvent.change(screen.getByRole("textbox"), { target: { value: JSON.stringify([ROW, ROW]) } });
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));

    expect(await screen.findByText("1 created, 1 failed.")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("A database with this name is already registered.")).toBeInTheDocument();
    expect(mockApiFetch).toHaveBeenCalledWith("/api/databases/bulk", { method: "POST", body: { databases: [ROW, ROW] } });
  });

  it("calls onDone when Done is clicked after results are shown", async () => {
    const onDone = vi.fn();
    mockApiFetch.mockResolvedValueOnce({
      created_count: 1,
      failed_count: 0,
      results: [{ index: 0, status: "created", database: { ...ROW, id: "db-1", is_active: true, tags: [], created_at: "", updated_at: "" } as never, error: null }],
    } satisfies BulkCreateDatabasesResponse);
    renderModal(onDone);

    fireEvent.change(screen.getByRole("textbox"), { target: { value: JSON.stringify([ROW]) } });
    await userEvent.click(screen.getByRole("button", { name: "Submit" }));
    await screen.findByText("1 created, 0 failed.");

    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalled();
  });
});
