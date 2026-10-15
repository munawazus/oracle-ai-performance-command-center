export function formatBytes(bytes: number | null): string {
  if (bytes === null) return "—";
  const sign = bytes < 0 ? "-" : "";
  const abs = Math.abs(bytes);
  if (abs >= 1024 ** 3) return `${sign}${(abs / 1024 ** 3).toFixed(1)} GB`;
  if (abs >= 1024 ** 2) return `${sign}${(abs / 1024 ** 2).toFixed(1)} MB`;
  if (abs >= 1024) return `${sign}${(abs / 1024).toFixed(1)} KB`;
  return `${sign}${Math.round(abs)} B`;
}
