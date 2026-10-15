import type { Tone } from "../components/ui/Badge";
import type { IncidentSeverity } from "../types/api";

export const SEVERITY_TONE: Record<IncidentSeverity, Tone> = {
  INFO: "neutral",
  LOW: "info",
  MEDIUM: "warn",
  HIGH: "crit",
  CRITICAL: "critical",
};

// Mirrors the backend's own SEVERITY_RANK (incident_service.py) - used to sort real anomalies
// worst-first, e.g. so a chart annotation keeps the most severe one when several land on the
// same point.
export const SEVERITY_RANK: Record<IncidentSeverity, number> = {
  INFO: 0,
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
};
