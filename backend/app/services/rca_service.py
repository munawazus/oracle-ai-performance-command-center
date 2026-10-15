"""Root cause analysis (master plan Sec 16): an enterprise-shaped RCA - executive summary,
impact, timeline, technical findings, root cause, contributing factors, evidence, resolution,
why the resolution worked, corrective/preventive actions, confidence, and limitations. Never
fabricates certainty - if the model's response can't be parsed as the required structure, or
the model itself is unreachable, that is surfaced honestly rather than papered over with a
plausible-sounding guess.

Timeline and technical findings/evidence are NOT trusted to the model to restate - they are
assembled deterministically in Python from the same shared app.ai.investigation evidence layer
the Copilot chat uses (see that module's docstring for why: a real, scored benchmark run and
this feature's own live QA both showed this platform's actual configured model restating a
correct, pre-correlated finding as a wrong number).

The LLM-authored narrative is itself split across TWO calls, not one wide one. Live QA against
this platform's real configured model found that a single ~15-field call made it abandon the
requested schema entirely and invent its own ad-hoc JSON structure - even with a worked example
and a large token budget - while the original, smaller 9-field shape has a real, scored 5/5
schema-adherence track record (docs/RCA_BENCHMARK_REPORT.md). So Call 1 (build_rca_messages)
asks only for that proven 9-field core; Call 2 (build_rca_narrative_messages) is a separate,
smaller, seeded follow-up for the newer enterprise-report fields. A Call 2 failure never fails
the whole RCA - Call 1's real investigative result is the valuable, already-proven part, and
degrading the narrative fields to their honest empty defaults is preferable to losing it.
"""

import json
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGatewayError, LLMClient
from app.ai.investigation import EvidenceBundle, Finding, ToolCallStatus, gather_evidence
from app.ai.prompts import build_rca_messages, build_rca_narrative_messages
from app.exceptions import UpstreamUnavailableError
from app.logging_config import get_logger
from app.services.audit_service import AuditService

logger = get_logger(__name__)

_VALID_CONFIDENCE = {"low", "medium", "high"}

# Public (not underscore-prefixed): app/rca_benchmark/scoring.py's check_schema_adherence
# checks for this exact string to detect the parse-failure fallback, a legitimate cross-module
# contract point, not an internal implementation detail.
UNPARSEABLE_NOTE = "The model did not return structured output that could be parsed. Treat this RCA as unavailable."

_EVENT_LABELS = {
    "opened": "Incident opened",
    "evidence_added": "New evidence correlated to this incident",
    "acknowledged": "Incident acknowledged",
    "assigned": "Incident assigned",
    "resolved": "Incident resolved",
}


@dataclass(frozen=True)
class TimelineEntry:
    """Code-computed, not LLM-authored - built directly from the incident's own real event
    history (app/ai/tools.py's get_incident), so a timeline entry can never be invented."""

    occurred_at: str
    label: str


@dataclass(frozen=True)
class RcaResult:
    # LLM-authored narrative (parsed from the model's JSON responses) - every field defaulted
    # so RcaResult(**parsed, model=model, ...) never depends on field order.
    executive_summary: str = ""
    impact: str = ""
    probable_root_cause: str | None = None
    confidence: str | None = None
    evidence_summary: str = ""
    contributing_factors: list[str] = field(default_factory=list)
    # Deprecated alias for contributing_factors, same values - kept for any existing reader of
    # the old field name (e.g. frontend/src/pages/IncidentsPage.tsx before its own Phase 6).
    secondary_contributors: list[str] = field(default_factory=list)
    alternative_hypotheses: list[str] = field(default_factory=list)
    recommended_investigation: list[str] = field(default_factory=list)
    recommended_action: list[str] = field(default_factory=list)
    resolution: str | None = None
    why_resolution_works: str | None = None
    corrective_preventive_actions: list[str] = field(default_factory=list)
    risk: str | None = None
    limitations: str = ""
    data_completeness_note: str = ""
    model: str | None = None
    # Code-computed structural sections - never parsed from the model's own text, set directly
    # by RcaService.investigate() from the shared evidence-investigation layer.
    timeline: list[TimelineEntry] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    evidence_coverage: str | None = None
    tool_statuses: list[ToolCallStatus] = field(default_factory=list)


def _as_string_list(value: object) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()


def parse_rca_response(raw: str) -> dict:
    """Call 1's response - the proven 9-field core."""
    try:
        data = json.loads(_strip_markdown_fence(raw))
        if not isinstance(data, dict):
            raise ValueError("response was not a JSON object")
    except (json.JSONDecodeError, ValueError):
        return {
            "probable_root_cause": None,
            "confidence": None,
            "evidence_summary": "",
            "contributing_factors": [],
            "secondary_contributors": [],
            "alternative_hypotheses": [],
            "recommended_investigation": [],
            "recommended_action": [],
            "risk": None,
            "data_completeness_note": UNPARSEABLE_NOTE,
        }

    confidence = data.get("confidence")
    if confidence not in _VALID_CONFIDENCE:
        confidence = None

    contributing_factors = _as_string_list(data.get("secondary_contributors"))

    return {
        "probable_root_cause": data.get("probable_root_cause") or None,
        "confidence": confidence,
        "evidence_summary": str(data.get("evidence_summary") or ""),
        "contributing_factors": contributing_factors,
        "secondary_contributors": contributing_factors,
        "alternative_hypotheses": _as_string_list(data.get("alternative_hypotheses")),
        "recommended_investigation": _as_string_list(data.get("recommended_investigation")),
        "recommended_action": _as_string_list(data.get("recommended_action")),
        "risk": data.get("risk") or None,
        "data_completeness_note": str(data.get("data_completeness_note") or ""),
    }


def _empty_narrative() -> dict:
    return {
        "executive_summary": "",
        "impact": "",
        "resolution": None,
        "why_resolution_works": None,
        "corrective_preventive_actions": [],
        "limitations": "",
    }


def parse_rca_narrative_response(raw: str) -> dict:
    """Call 2's response - the smaller, seeded enterprise-report follow-up. Never raises -
    an unparseable or missing narrative degrades to honest empty defaults, matching
    parse_rca_response's own fallback philosophy, since Call 1's real result must never be
    lost because Call 2 didn't come back cleanly."""
    try:
        data = json.loads(_strip_markdown_fence(raw))
        if not isinstance(data, dict):
            raise ValueError("response was not a JSON object")
    except (json.JSONDecodeError, ValueError):
        return _empty_narrative()

    return {
        "executive_summary": str(data.get("executive_summary") or ""),
        "impact": str(data.get("impact") or ""),
        "resolution": data.get("resolution") or None,
        "why_resolution_works": data.get("why_resolution_works") or None,
        "corrective_preventive_actions": _as_string_list(data.get("corrective_preventive_actions")),
        "limitations": str(data.get("limitations") or ""),
    }


def _build_timeline(incident_evidence: dict) -> list[TimelineEntry]:
    if not incident_evidence.get("available"):
        return []
    return [
        TimelineEntry(
            occurred_at=e["occurred_at"],
            label=_EVENT_LABELS.get(e["event_type"], e["event_type"].replace("_", " ").capitalize()),
        )
        for e in incident_evidence.get("timeline", [])
    ]


async def gather_rca_evidence(db: AsyncSession, incident_id: str) -> dict:
    """Thin wrapper kept for external callers that only need the raw evidence dict, not the
    full EvidenceBundle - app/rca_benchmark/scoring.py's no-fabricated-identifiers check and
    backend/scripts/rca_benchmark.py both reconstruct the exact evidence a run was scored
    against this way. RcaService.investigate() itself calls gather_evidence() directly (below),
    since it also needs the findings/coverage/tool_statuses the bare dict doesn't carry.
    Raises NotFoundError if the incident doesn't exist (via gather_evidence)."""
    bundle = await gather_evidence(db, database_id=None, incident_id=incident_id)
    return bundle.raw_evidence


class RcaService:
    def __init__(self, db: AsyncSession, llm: LLMClient, audit: AuditService):
        self.db = db
        self.llm = llm
        self.audit = audit

    async def investigate(self, incident_id: str, *, actor_user_id: str, ip_address: str | None) -> RcaResult:
        bundle = await gather_evidence(self.db, database_id=None, incident_id=incident_id)

        # Call 1: the proven 9-field investigative core. A failure here fails the whole RCA -
        # this is the real, load-bearing content the feature exists to produce.
        core_messages = build_rca_messages(bundle)
        try:
            core_content, model = await self.llm.complete(core_messages, max_tokens=1500, json_mode=True)
        except AIGatewayError as exc:
            await self.audit.record(
                action="ai.rca_requested",
                success=False,
                actor_user_id=actor_user_id,
                target_type="incident",
                target_id=incident_id,
                ip_address=ip_address,
            )
            raise UpstreamUnavailableError("The AI gateway is currently unavailable.") from exc

        core = parse_rca_response(core_content)

        # Call 2: the smaller, seeded enterprise-report follow-up. Never fails the whole RCA -
        # Call 1's real result above is preserved regardless of what happens here.
        narrative = await self._expand_narrative(bundle, core)

        await self.audit.record(
            action="ai.rca_requested",
            success=True,
            actor_user_id=actor_user_id,
            target_type="incident",
            target_id=incident_id,
            ip_address=ip_address,
            metadata={
                "model": model,
                "confidence": core["confidence"],
                "evidence_coverage": bundle.coverage.coverage_label,
            },
        )
        return RcaResult(
            **core,
            **narrative,
            model=model,
            timeline=_build_timeline(bundle.raw_evidence.get("incident", {})),
            findings=bundle.findings,
            evidence_coverage=bundle.coverage.coverage_label,
            tool_statuses=bundle.tool_statuses,
        )

    async def _expand_narrative(self, bundle: EvidenceBundle, core: dict) -> dict:
        messages = build_rca_narrative_messages(bundle, core)
        try:
            content, _ = await self.llm.complete(messages, max_tokens=900, json_mode=True)
        except AIGatewayError:
            logger.warning("rca_narrative_call_failed")
            return _empty_narrative()
        return parse_rca_narrative_response(content)
