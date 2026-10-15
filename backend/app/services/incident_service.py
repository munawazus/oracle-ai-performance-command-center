"""Deduplicates anomalies into incidents. Master plan Sec 7: "Do not create one incident for
every individual alert when multiple alerts are symptoms of the same event." v1 correlation
rule: anomalies for the same database within CORRELATION_WINDOW of an incident's last
evidence attach to that incident instead of opening a new one. Cross-database or
cross-root-cause correlation (e.g. "this SQL regression is causing that wait spike") is a
later, AI-assisted phase - this is the deterministic floor it builds on.
"""

from datetime import datetime, timedelta

from app.exceptions import NotFoundError
from app.models.analytics import Anomaly
from app.models.incident import Incident
from app.oracle.connector import OracleConnectError
from app.oracle.params import public_message
from app.repositories.incident_repo import IncidentRepository
from app.services.audit_service import AuditService

CORRELATION_WINDOW = timedelta(minutes=30)

SEVERITY_RANK = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

# A reachability failure means zero telemetry can even be sampled - always the most severe
# case a monitoring tool can report, never a statistical judgment call the way a metric
# anomaly's z-score is. Fixed severity, not derived.
UNREACHABLE_SEVERITY = "CRITICAL"

_REACHABILITY_TITLES = {
    OracleConnectError.CATEGORY_UNREACHABLE: "Database unreachable",
    OracleConnectError.CATEGORY_AUTH_FAILED: "Database authentication failing",
    OracleConnectError.CATEGORY_UNKNOWN: "Database connection failing",
}


def humanize_reachability_category(category: str) -> str:
    return _REACHABILITY_TITLES.get(category, _REACHABILITY_TITLES[OracleConnectError.CATEGORY_UNKNOWN])


def humanize_metric_key(metric_key: str) -> str:
    if metric_key == "active_session_count":
        return "Active session count"
    if metric_key == "cpu_centisec_used_per_sec":
        return "Database CPU usage"
    if metric_key.startswith("wait_rate_centisec_per_sec:"):
        wait_class = metric_key.split(":", 1)[1]
        return f"{wait_class} wait time"
    return metric_key


class IncidentService:
    def __init__(self, incident_repo: IncidentRepository, audit: AuditService | None = None):
        self.incident_repo = incident_repo
        self.audit = audit

    async def correlate_anomaly(self, anomaly: Anomaly) -> Incident:
        existing = await self.incident_repo.find_open_within_window(
            anomaly.monitored_database_id, CORRELATION_WINDOW, now=anomaly.detected_at
        )
        if existing is not None:
            await self._attach_evidence(existing, anomaly)
            return existing
        return await self._open_incident(anomaly)

    async def _open_incident(self, anomaly: Anomaly) -> Incident:
        incident = await self.incident_repo.create(
            monitored_database_id=anomaly.monitored_database_id,
            severity=anomaly.severity,
            title=f"{humanize_metric_key(anomaly.metric_key)} anomaly",
            opened_at=anomaly.detected_at,
            last_evidence_at=anomaly.detected_at,
        )
        await self.incident_repo.link_anomaly(anomaly.id, incident.id)
        await self.incident_repo.add_event(
            incident.id,
            "opened",
            detail={"metric_key": anomaly.metric_key, "severity": anomaly.severity, "observed_value": anomaly.observed_value},
        )
        return incident

    async def open_or_attach_reachability_incident(
        self, database_id: str, *, category: str, detected_at: datetime
    ) -> Incident:
        """A reachability failure never produces an Anomaly row - there's no baseline/z-score
        concept for a binary up/down signal, so fabricating one just to reuse
        correlate_anomaly would mean inventing meaningless baseline stats. IncidentRepository
        itself never actually required an Anomaly (create() takes plain scalars; link_anomaly
        is a separate, optional call) - this reimplements the same correlation-window dedup
        correlate_anomaly uses, directly, without the detour through a synthetic anomaly.
        Severity is always UNREACHABLE_SEVERITY, so there's no escalation case to handle,
        unlike _attach_evidence's metric-anomaly path."""
        existing = await self.incident_repo.find_open_within_window(
            database_id, CORRELATION_WINDOW, now=detected_at
        )
        if existing is not None:
            await self.incident_repo.update_evidence(existing.id, last_evidence_at=detected_at, new_severity=None)
            await self.incident_repo.add_event(
                existing.id, "evidence_added", detail={"reachability_category": category}
            )
            return existing

        incident = await self.incident_repo.create(
            monitored_database_id=database_id,
            severity=UNREACHABLE_SEVERITY,
            title=humanize_reachability_category(category),
            opened_at=detected_at,
            last_evidence_at=detected_at,
        )
        await self.incident_repo.add_event(
            incident.id,
            "opened",
            detail={"reachability_category": category, "message": public_message(category)},
        )
        return incident

    async def _attach_evidence(self, incident: Incident, anomaly: Anomaly) -> None:
        await self.incident_repo.link_anomaly(anomaly.id, incident.id)
        escalated = SEVERITY_RANK[anomaly.severity] > SEVERITY_RANK[incident.severity]
        await self.incident_repo.update_evidence(
            incident.id,
            last_evidence_at=anomaly.detected_at,
            new_severity=anomaly.severity if escalated else None,
        )
        await self.incident_repo.add_event(
            incident.id,
            "evidence_added",
            detail={
                "metric_key": anomaly.metric_key,
                "severity": anomaly.severity,
                "observed_value": anomaly.observed_value,
                "escalated": escalated,
            },
        )

    async def acknowledge(self, incident_id: str, *, actor_user_id: str, ip_address: str | None) -> Incident:
        incident = await self.incident_repo.acknowledge(incident_id, actor_user_id)
        if incident is None:
            raise NotFoundError("Incident not found.")
        await self.incident_repo.add_event(incident_id, "acknowledged", actor_user_id=actor_user_id)
        if self.audit:
            await self.audit.record(
                action="incident.acknowledged",
                success=True,
                actor_user_id=actor_user_id,
                target_type="incident",
                target_id=incident_id,
                ip_address=ip_address,
            )
        return incident

    async def assign(
        self, incident_id: str, assignee_user_id: str, *, actor_user_id: str, ip_address: str | None
    ) -> Incident:
        incident = await self.incident_repo.assign(incident_id, assignee_user_id)
        if incident is None:
            raise NotFoundError("Incident not found.")
        await self.incident_repo.add_event(
            incident_id, "assigned", actor_user_id=actor_user_id, detail={"assigned_to_user_id": assignee_user_id}
        )
        if self.audit:
            await self.audit.record(
                action="incident.assigned",
                success=True,
                actor_user_id=actor_user_id,
                target_type="incident",
                target_id=incident_id,
                ip_address=ip_address,
                metadata={"assigned_to_user_id": assignee_user_id},
            )
        return incident

    async def resolve(
        self, incident_id: str, *, actor_user_id: str, note: str | None, ip_address: str | None
    ) -> Incident:
        incident = await self.incident_repo.resolve(incident_id, actor_user_id, note)
        if incident is None:
            raise NotFoundError("Incident not found.")
        await self.incident_repo.add_event(
            incident_id, "resolved", actor_user_id=actor_user_id, detail={"note": note} if note else None
        )
        if self.audit:
            await self.audit.record(
                action="incident.resolved",
                success=True,
                actor_user_id=actor_user_id,
                target_type="incident",
                target_id=incident_id,
                ip_address=ip_address,
            )
        return incident
