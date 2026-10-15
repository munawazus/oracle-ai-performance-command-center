import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WaitClassSmallMultiples } from "./WaitClassSmallMultiples";

// Recharts' ResponsiveContainer measures 0x0 in jsdom (no ResizeObserver), so the axis/area
// internals never actually draw - these assert the surrounding chrome (one tile per series,
// titled by label) rather than chart internals, matching TrendChart's own established
// jsdom-limitation workaround. Real rendering (including the reference line) is verified live.
describe("WaitClassSmallMultiples", () => {
  it("renders one tile per series, each titled by its label", () => {
    render(
      <WaitClassSmallMultiples
        data={[{ time: "t0", CPU: 0.1, "User I/O": 0.5 }]}
        xKey="time"
        series={[
          { key: "CPU", label: "CPU" },
          { key: "User I/O", label: "User I/O" },
        ]}
      />,
    );

    expect(screen.getByText("CPU")).toBeInTheDocument();
    expect(screen.getByText("User I/O")).toBeInTheDocument();
  });

  it("accepts a reference line without crashing when a CPU tile is present", () => {
    render(
      <WaitClassSmallMultiples
        data={[{ time: "t0", CPU: 0.1, "User I/O": 0.5 }]}
        xKey="time"
        series={[
          { key: "CPU", label: "CPU" },
          { key: "User I/O", label: "User I/O" },
        ]}
        referenceLine={{ value: 22, label: "Max CPU cores" }}
      />,
    );

    expect(screen.getByText("CPU")).toBeInTheDocument();
  });

  it("renders no tiles when there is no series at all", () => {
    const { container } = render(<WaitClassSmallMultiples data={[]} xKey="time" series={[]} />);
    expect(container.textContent).toBe("");
  });
});
