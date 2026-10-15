"""Plain-text incident email content. Deliberately does not claim a probable cause,
confidence, or recommended action - there is no AI/RCA layer yet (a later phase), and
master plan Sec 2/16 forbid fabricating that kind of certainty. What's here is only what
this phase can honestly say: what fired, on what database, with how much evidence.
"""

from app.models.database import MonitoredDatabase
from app.models.incident import Incident


def render_incident_email(
    incident: Incident, database: MonitoredDatabase, evidence_count: int, dashboard_url: str
) -> tuple[str, str]:
    subject = f"[{incident.severity}] {incident.title} - {database.name}"

    body = (
        "Oracle AI Performance Command Center - Incident Alert\n\n"
        f"Database:  {database.name} ({database.edition_hint}, {database.host}:{database.port}/{database.service_name})\n"
        f"Severity:  {incident.severity}\n"
        f"Incident:  {incident.title}\n"
        f"Status:    {incident.status}\n"
        f"Opened:    {incident.opened_at.isoformat()}\n"
        f"Evidence:  {evidence_count} anomaly observation(s) correlated so far\n\n"
        "Automated root-cause analysis is not available in this deployment yet - review the "
        "incident and its evidence directly:\n"
        f"{dashboard_url}\n\n"
        "This is an automated alert. Do not reply to this message.\n"
    )
    return subject, body
