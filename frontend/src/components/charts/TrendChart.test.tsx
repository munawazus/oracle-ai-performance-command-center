import { describe, expect, it } from "vitest";

import { assignSeriesColors, bucketToPeaks, toggleHiddenKey } from "./TrendChart";

describe("assignSeriesColors", () => {
  it("assigns a color to every key", () => {
    const colors = assignSeriesColors(["User I/O", "Commit", "System I/O"], false);
    expect(Object.keys(colors)).toHaveLength(3);
    expect(colors["User I/O"]).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("stays stable across renders even when key order changes (rates reorder by rank)", () => {
    const first = assignSeriesColors(["User I/O", "Commit", "System I/O"], false);
    const second = assignSeriesColors(["System I/O", "User I/O", "Commit"], false);
    expect(second).toEqual(first);
  });

  it("uses different hex values for light and dark mode", () => {
    const light = assignSeriesColors(["User I/O"], false);
    const dark = assignSeriesColors(["User I/O"], true);
    expect(light["User I/O"]).not.toBe(dark["User I/O"]);
  });

  it("wraps around the palette when there are more keys than colors", () => {
    const keys = Array.from({ length: 10 }, (_, i) => `Class ${i}`);
    const colors = assignSeriesColors(keys, false);
    expect(Object.keys(colors)).toHaveLength(10);
  });

  it("gives the literal key 'CPU' its own reserved color instead of a categorical slot", () => {
    const withCpu = assignSeriesColors(["CPU", "Application"], false);
    const withoutCpu = assignSeriesColors(["Application"], false);
    // Application's color is unaffected by CPU's presence - CPU never displaces a categorical
    // slot, it sits outside the rotation entirely.
    expect(withCpu["Application"]).toBe(withoutCpu["Application"]);
    expect(withCpu["CPU"]).not.toBe(withCpu["Application"]);
  });
});

describe("bucketToPeaks", () => {
  it("returns the data unchanged when it already fits within maxPoints", () => {
    const data = Array.from({ length: 50 }, (_, i) => ({ time: `t${i}`, value: i }));
    expect(bucketToPeaks(data, "time", ["value"], 200)).toBe(data);
  });

  it("keeps a brief real spike visible - the whole point of this function", () => {
    // 1000 quiet samples (value=1) with a single real spike of 12 buried in the middle -
    // exactly the "11x baseline spike among 24h of data" shape confirmed live against the
    // real database (docs/CHART_QUALITY_PLAN.md).
    const data = Array.from({ length: 1000 }, (_, i) => ({ time: `t${i}`, value: i === 500 ? 12 : 1 }));
    const bucketed = bucketToPeaks(data, "time", ["value"], 200);

    expect(bucketed.length).toBeLessThan(data.length);
    expect(Math.max(...bucketed.map((b) => b.value as number))).toBe(12);
  });

  it("takes the max per bucket, never an average that would smooth a spike away", () => {
    const data = Array.from({ length: 100 }, (_, i) => ({ time: `t${i}`, value: i < 50 ? 0 : 100 }));
    const bucketed = bucketToPeaks(data, "time", ["value"], 10);

    // An average across a bucket straddling the 0->100 jump would report something between -
    // the max must not be diluted down from the real 100 that occurred.
    expect(bucketed.some((b) => b.value === 100)).toBe(true);
  });

  it("labels each bucket with its last point's x value", () => {
    const data = Array.from({ length: 10 }, (_, i) => ({ time: `t${i}`, value: 1 }));
    const bucketed = bucketToPeaks(data, "time", ["value"], 2);
    // 10 points into 2 buckets -> bucket size 5 -> last points are indices 4 and 9.
    expect(bucketed.map((b) => b.time)).toEqual(["t4", "t9"]);
  });

  it("buckets multiple series independently", () => {
    const data = [
      { time: "t0", a: 1, b: 9 },
      { time: "t1", a: 5, b: 2 },
    ];
    const bucketed = bucketToPeaks(data, "time", ["a", "b"], 1);
    expect(bucketed).toEqual([{ time: "t1", a: 5, b: 9 }]);
  });
});

describe("toggleHiddenKey", () => {
  it("adds a key that isn't hidden yet", () => {
    const result = toggleHiddenKey(new Set(), "Commit");
    expect(result.has("Commit")).toBe(true);
  });

  it("removes a key that's already hidden", () => {
    const result = toggleHiddenKey(new Set(["Commit"]), "Commit");
    expect(result.has("Commit")).toBe(false);
  });

  it("doesn't mutate the input set", () => {
    const original = new Set(["Commit"]);
    toggleHiddenKey(original, "User I/O");
    expect(original.has("User I/O")).toBe(false);
    expect(original.size).toBe(1);
  });

  it("leaves other hidden keys untouched", () => {
    const result = toggleHiddenKey(new Set(["Commit", "System I/O"]), "Commit");
    expect(result.has("System I/O")).toBe(true);
    expect(result.has("Commit")).toBe(false);
  });
});
