import { describe, expect, it } from "vitest";

import { formatBytes } from "./bytes";

describe("formatBytes", () => {
  it("returns an em dash for null", () => {
    expect(formatBytes(null)).toBe("—");
  });

  it("formats zero and sub-KB values as rounded bytes", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(511.7)).toBe("512 B");
  });

  it("formats KB/MB/GB boundaries", () => {
    expect(formatBytes(1024)).toBe("1.0 KB");
    expect(formatBytes(1024 ** 2)).toBe("1.0 MB");
    expect(formatBytes(1024 ** 3)).toBe("1.0 GB");
  });

  it("formats a negative (shrinking) value with a rounded, unit-scaled magnitude", () => {
    // The real value seen live on /capacity for a shrinking tablespace.
    expect(formatBytes(-11363580.403086632)).toBe("-10.8 MB");
  });

  it("formats a negative sub-KB value as rounded bytes, not a raw float", () => {
    expect(formatBytes(-42.9)).toBe("-43 B");
  });
});
