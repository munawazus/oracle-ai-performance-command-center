import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CompareModePanel, compareStatus, computeDeviationPosition } from "./CompareModePanel";
import type { MetricCompareResponse } from "../../types/api";

const mockApiFetch = vi.fn();

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return { ...actual, apiFetch: (...args: unknown[]) => mockApiFetch(...args) };
});

function renderPanel(databaseId = "db-1", metricKey = "active_session_count", valueSuffix = "") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CompareModePanel databaseId={databaseId} metricKey={metricKey} valueSuffix={valueSuffix} />
    </QueryClientProvider>,
  );
}

const BASE: MetricCompareResponse = {
  available: true,
  reason: null,
  metric_key: "active_session_count",
  metric_label: "Active session count",
  hour_of_day: 12,
  observed_value: 18,
  baseline_mean: 15,
  baseline_stddev: 3,
  sample_count: 42,
  min_samples_for_anomaly: 20,
  z_score: 1,
  severity: null,
  baseline_updated_at: "2026-09-01T12:00:00Z",
};

describe("computeDeviationPosition", () => {
  it("centers an observed value equal to the mean", () => {
    expect(computeDeviationPosition(15, 15, 3)).toBe(50);
  });

  it("positions +1 stddev at the expected fraction along a ±3 sigma track", () => {
    // +1 sigma out of a ±3 sigma track: (1 + 3) / 6 = 4/6 = 66.67%
    expect(computeDeviationPosition(18, 15, 3)).toBeCloseTo((4 / 6) * 100, 5);
  });

  it("clamps an extreme deviation to the track's edge", () => {
    expect(computeDeviationPosition(1000, 15, 3)).toBe(100);
    expect(computeDeviationPosition(-1000, 15, 3)).toBe(0);
  });

  it("returns null when there is no variance to position against", () => {
    expect(computeDeviationPosition(18, 15, 0)).toBeNull();
    expect(computeDeviationPosition(18, 15, -1)).toBeNull();
  });
});

describe("compareStatus", () => {
  it("maps each real backend severity through the shared severity tone map", () => {
    expect(compareStatus("LOW", 42, 20)).toEqual({ label: "LOW", tone: "info" });
    expect(compareStatus("MEDIUM", 42, 20)).toEqual({ label: "MEDIUM", tone: "warn" });
    expect(compareStatus("HIGH", 42, 20)).toEqual({ label: "HIGH", tone: "crit" });
    expect(compareStatus("CRITICAL", 42, 20)).toEqual({ label: "CRITICAL", tone: "critical" });
  });

  it("reports Normal when the backend found no severity but enough samples exist", () => {
    expect(compareStatus(null, 42, 20)).toEqual({ label: "Normal", tone: "good" });
  });

  it("reports Building baseline when there aren't enough samples yet, regardless of severity", () => {
    expect(compareStatus(null, 5, 20)).toEqual({ label: "Building baseline", tone: "neutral" });
  });
});

describe("CompareModePanel", () => {
  it("shows an honest empty state when the metric has no current value", async () => {
    mockApiFetch.mockResolvedValue({
      ...BASE,
      available: false,
      reason: "No current telemetry for this metric yet.",
      sample_count: 0,
    });
    renderPanel();

    expect(await screen.findByText("No current value for this metric")).toBeInTheDocument();
    expect(screen.getByText("No current telemetry for this metric yet.")).toBeInTheDocument();
  });

  it("shows a building-baseline state when a current value exists but no baseline yet", async () => {
    mockApiFetch.mockResolvedValue({
      ...BASE,
      baseline_mean: null,
      baseline_stddev: null,
      sample_count: 0,
      severity: null,
    });
    renderPanel();

    expect(await screen.findByText("18.0")).toBeInTheDocument();
    expect(screen.getByText("Still building this hour's baseline (0 of 20 samples needed).")).toBeInTheDocument();
    expect(screen.getByText("Building baseline")).toBeInTheDocument();
  });

  it("renders a full comparison with the real severity badge", async () => {
    mockApiFetch.mockResolvedValue({ ...BASE, severity: "HIGH" });
    renderPanel();

    expect(await screen.findByText("18.0")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(screen.getByText("Baseline 15.0 ± 3.0 (42 of 20+ samples)")).toBeInTheDocument();
  });

  it("colors the panel's accent border and value by the real severity, not always the same tone", async () => {
    mockApiFetch.mockResolvedValue({ ...BASE, severity: "CRITICAL" });
    const { container } = renderPanel();

    const value = await screen.findByText("18.0");
    expect(value.className).toContain("text-red-600");
    expect(container.querySelector(".border-l-4")?.className).toContain("border-l-red-600");
  });

  it("appends a given valueSuffix to the current value and both baseline numbers", async () => {
    mockApiFetch.mockResolvedValue({ ...BASE, severity: "HIGH" });
    renderPanel("db-1", "active_session_count", "%");

    // The value and its suffix render as separate elements (for typographic hierarchy), so
    // plain string matching can't find "18.0%" - match on the parent's combined text instead.
    expect(await screen.findByText((_, el) => el?.textContent === "18.0%")).toBeInTheDocument();
    expect(screen.getByText("Baseline 15.0% ± 3.0% (42 of 20+ samples)")).toBeInTheDocument();
  });

  it("percent-encodes a metric_key containing special characters in the request", async () => {
    mockApiFetch.mockResolvedValue({ ...BASE, metric_key: "wait_rate_centisec_per_sec:User I/O" });
    renderPanel("db-1", "wait_rate_centisec_per_sec:User I/O");

    await screen.findByText("18.0");
    expect(mockApiFetch).toHaveBeenCalledWith(
      "/api/databases/db-1/metrics/compare?metric_key=wait_rate_centisec_per_sec%3AUser%20I%2FO",
    );
  });
});
