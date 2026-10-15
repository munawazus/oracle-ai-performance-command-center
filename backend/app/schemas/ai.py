from pydantic import Field

from app.ai.investigation import Finding, ToolCallStatus
from app.schemas.common import ApiModel


class CopilotAskRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2000)
    database_id: str | None = None
    incident_id: str | None = None
    # Explicit "Ask about the whole fleet" toggle (Activity tab Phase 4b) - never inferred from
    # the question text. When true, database_id/incident_id are ignored and CopilotService
    # gathers evidence across every active database instead of one.
    fleet_scope: bool = False


class ToolStatusView(ApiModel):
    tool_name: str
    called: bool
    returned_evidence: bool
    reason: str | None = None


class FindingView(ApiModel):
    """One pre-correlated fact (backend/app/ai/investigation.py's Finding) - a category,
    severity, and one-sentence summary naming concrete entities, plus the freshness of the
    evidence it was built from where the source tool exposes one."""

    finding_id: str
    category: str
    severity: str
    summary: str
    entities: dict
    sampled_at: str | None = None
    age_seconds: float | None = None
    is_stale: bool | None = None


class CopilotAskResponse(ApiModel):
    answer: str
    model: str | None
    tools_used: list[str]
    tool_statuses: list[ToolStatusView] = []
    evidence_coverage: str | None = None
    findings: list[FindingView] = []


class TimelineEntryView(ApiModel):
    """One event from the incident's own real history (backend/app/services/rca_service.py's
    TimelineEntry) - code-computed, never LLM-authored, so a timeline entry can never be
    invented."""

    occurred_at: str
    label: str


class RcaResponse(ApiModel):
    # LLM-authored narrative
    executive_summary: str = ""
    impact: str = ""
    probable_root_cause: str | None
    confidence: str | None
    evidence_summary: str
    contributing_factors: list[str] = []
    secondary_contributors: list[str]  # deprecated alias for contributing_factors, same values
    alternative_hypotheses: list[str]
    recommended_investigation: list[str]
    recommended_action: list[str]
    resolution: str | None = None
    why_resolution_works: str | None = None
    corrective_preventive_actions: list[str] = []
    risk: str | None
    limitations: str = ""
    data_completeness_note: str
    model: str | None
    # Code-computed structural sections
    timeline: list[TimelineEntryView] = []
    findings: list[FindingView] = []
    evidence_coverage: str | None = None
    tool_statuses: list[ToolStatusView] = []


def finding_to_view(finding: Finding) -> FindingView:
    """Shared by /api/ai/ask and /api/incidents/{id}/rca - both surface the same
    app.ai.investigation.Finding shape, so the flattening (nested Freshness -> three plain
    fields) exists exactly once."""
    freshness = finding.freshness
    return FindingView(
        finding_id=finding.finding_id,
        category=finding.category,
        severity=finding.severity,
        summary=finding.summary,
        entities=finding.entities,
        sampled_at=freshness.sampled_at.isoformat() if freshness else None,
        age_seconds=freshness.age_seconds if freshness else None,
        is_stale=freshness.is_stale if freshness else None,
    )


def tool_status_to_view(status: ToolCallStatus) -> ToolStatusView:
    return ToolStatusView(
        tool_name=status.tool_name, called=status.called, returned_evidence=status.returned_evidence, reason=status.reason
    )
