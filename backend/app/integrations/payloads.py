"""Renders one outbound payload per integration type from an incident + its database. Built
once at queue time (app/services/integration_service.py) and stored on IntegrationDelivery,
so what actually gets delivered never drifts from what was decided even if the incident
changes before the worker gets to it - same reasoning as NotificationEvent pre-rendering the
email subject/body rather than the worker re-rendering at send time.
"""

from app.models.database import MonitoredDatabase
from app.models.incident import Incident
from app.models.integration import INTEGRATION_TYPE_SLACK, INTEGRATION_TYPE_TEAMS, INTEGRATION_TYPE_WEBHOOK

_SEVERITY_COLOR = {
    "INFO": "#64748b",
    "LOW": "#3b82f6",
    "MEDIUM": "#f59e0b",
    "HIGH": "#ef4444",
    "CRITICAL": "#b91c1c",
}


def _incident_url(incident: Incident, dashboard_base_url: str) -> str:
    return f"{dashboard_base_url}/incidents?database_id={incident.monitored_database_id}"


def build_webhook_payload(incident: Incident, database: MonitoredDatabase, dashboard_base_url: str) -> dict:
    """Generic JSON - not tied to any receiver's schema. A custom receiver reads this
    directly; it's also the shape signed by the optional X-Signature-256 HMAC header."""
    return {
        "event": "incident.threshold_reached",
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "opened_at": incident.opened_at.isoformat(),
            "last_evidence_at": incident.last_evidence_at.isoformat(),
        },
        "database": {"id": database.id, "name": database.name, "edition_hint": database.edition_hint},
        "url": _incident_url(incident, dashboard_base_url),
    }


def build_slack_payload(incident: Incident, database: MonitoredDatabase, dashboard_base_url: str) -> dict:
    """Slack's incoming-webhook format (a plain POST of this JSON to the webhook URL - no
    OAuth app/bot token needed)."""
    url = _incident_url(incident, dashboard_base_url)
    text = f"[{incident.severity}] {database.name}: {incident.title}"
    return {
        "text": text,
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*{text}*\n<{url}|Open incident>"}},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Status: {incident.status}"},
                    {"type": "mrkdwn", "text": f"Opened: {incident.opened_at.isoformat()}"},
                ],
            },
        ],
    }


def build_teams_payload(incident: Incident, database: MonitoredDatabase, dashboard_base_url: str) -> dict:
    """Microsoft Teams' classic "Incoming Webhook" connector card format (MessageCard) - the
    simple POST-a-JSON-card format; Teams' newer Adaptive Card / Workflows path needs an
    app registration this platform doesn't have."""
    url = _incident_url(incident, dashboard_base_url)
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "summary": f"{incident.severity} incident on {database.name}",
        "themeColor": _SEVERITY_COLOR.get(incident.severity, "#64748b").lstrip("#"),
        "title": f"[{incident.severity}] {database.name}",
        "text": incident.title,
        "sections": [
            {
                "facts": [
                    {"name": "Status", "value": incident.status},
                    {"name": "Opened", "value": incident.opened_at.isoformat()},
                ]
            }
        ],
        "potentialAction": [
            {"@type": "OpenUri", "name": "Open incident", "targets": [{"os": "default", "uri": url}]}
        ],
    }


_BUILDERS = {
    INTEGRATION_TYPE_WEBHOOK: build_webhook_payload,
    INTEGRATION_TYPE_SLACK: build_slack_payload,
    INTEGRATION_TYPE_TEAMS: build_teams_payload,
}


def build_payload(integration_type: str, incident: Incident, database: MonitoredDatabase, dashboard_base_url: str) -> dict:
    builder = _BUILDERS.get(integration_type, build_webhook_payload)
    return builder(incident, database, dashboard_base_url)
