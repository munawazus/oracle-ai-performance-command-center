from dataclasses import dataclass, field

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import AIGatewayError, LLMClient
from app.ai.investigation import (
    Finding,
    ToolCallStatus,
    gather_evidence,
    gather_fleet_evidence,
    render_findings_as_facts,
)
from app.ai.prompts import build_copilot_messages, build_copilot_messages_fleet, build_copilot_messages_no_context
from app.exceptions import UpstreamUnavailableError
from app.services.audit_service import AuditService


@dataclass(frozen=True)
class CopilotAnswer:
    answer: str
    model: str | None
    tools_used: list[str]
    tool_statuses: list[ToolCallStatus] = field(default_factory=list)
    evidence_coverage: str | None = None
    findings: list[Finding] = field(default_factory=list)


class CopilotService:
    """Deterministic evidence gathering (not LLM-driven tool selection) - this v1 has no
    real local model to validate function-calling reliability against, so tool SELECTION
    is fixed by what context the caller provides (database_id/incident_id); only the
    synthesis into a natural-language answer goes through the model. See docs/RUNBOOK.md.

    Even with no database_id/incident_id at all, the model still runs (see
    build_copilot_messages_no_context) - there is no evidence to gather in that case, but a
    real in-character reply beats a hardcoded canned string that never touches the model."""

    def __init__(self, db: AsyncSession, llm: LLMClient, audit: AuditService, redis: Redis | None = None):
        self.db = db
        self.llm = llm
        self.audit = audit
        self.redis = redis

    async def ask(
        self,
        question: str,
        *,
        database_id: str | None,
        incident_id: str | None,
        fleet_scope: bool = False,
        actor_user_id: str,
        ip_address: str | None,
    ) -> CopilotAnswer:
        if fleet_scope:
            return await self._ask_fleet(question, actor_user_id=actor_user_id, ip_address=ip_address)

        if not database_id and not incident_id:
            # No evidence to gather at all - but a plain greeting still deserves a real,
            # in-character reply, not a hardcoded string that never touches the model (the
            # previous behavior, and the thing that made every "hi" look like a static,
            # robotic non-answer). The no-context prompt still forbids fabricating any
            # technical claim, since there is genuinely zero evidence here.
            messages = build_copilot_messages_no_context(question)
            try:
                content, model = await self.llm.complete(messages)
            except AIGatewayError as exc:
                await self.audit.record(
                    action="ai.copilot_query",
                    success=False,
                    actor_user_id=actor_user_id,
                    target_type=None,
                    target_id=None,
                    ip_address=ip_address,
                    metadata={"tools_used": [], "question_preview": question[:200]},
                )
                raise UpstreamUnavailableError("The AI gateway is currently unavailable.") from exc

            await self.audit.record(
                action="ai.copilot_query",
                success=True,
                actor_user_id=actor_user_id,
                target_type=None,
                target_id=None,
                ip_address=ip_address,
                metadata={"tools_used": [], "model": model, "question_preview": question[:200]},
            )
            return CopilotAnswer(answer=content.strip(), model=model, tools_used=[])

        # get_sql_plan is deliberately NOT gathered unconditionally here - it needs a specific
        # sql_id and a full plan body would blow the prompt budget for every question. It stays
        # a targeted, on-demand tool (Enterprise Readiness Plan D.1).
        bundle = await gather_evidence(self.db, database_id=database_id, incident_id=incident_id)
        tools_used = [s.tool_name for s in bundle.tool_statuses if s.called]

        messages = build_copilot_messages(question, bundle)
        try:
            content, model = await self.llm.complete(messages)
        except AIGatewayError as exc:
            await self.audit.record(
                action="ai.copilot_query",
                success=False,
                actor_user_id=actor_user_id,
                target_type="monitored_database" if bundle.database_id else "incident",
                target_id=bundle.database_id or bundle.incident_id,
                ip_address=ip_address,
                metadata={"tools_used": tools_used, "question_preview": question[:200]},
            )
            raise UpstreamUnavailableError("The AI gateway is currently unavailable.") from exc

        await self.audit.record(
            action="ai.copilot_query",
            success=True,
            actor_user_id=actor_user_id,
            target_type="monitored_database" if bundle.database_id else "incident",
            target_id=bundle.database_id or bundle.incident_id,
            ip_address=ip_address,
            metadata={
                "tools_used": tools_used,
                "model": model,
                "question_preview": question[:200],
                "evidence_coverage": bundle.coverage.coverage_label,
            },
        )
        # The facts a user sees are never trusted to the model to restate accurately - live QA
        # against this platform's real configured model showed it turning a real "37 waiters"
        # finding into a fabricated "200" or "18" in its own prose, even when explicitly told
        # not to (see build_copilot_messages' _FINDINGS_COMMENTARY_RULES for the full story).
        # So when a finding exists, the facts are assembled here, deterministically, and the
        # model's own text is appended only as commentary underneath - never substituted.
        answer = f"{render_findings_as_facts(bundle.findings)}\n\n{content.strip()}" if bundle.findings else content

        return CopilotAnswer(
            answer=answer,
            model=model,
            tools_used=tools_used,
            tool_statuses=bundle.tool_statuses,
            evidence_coverage=bundle.coverage.coverage_label,
            findings=bundle.findings,
        )

    async def _ask_fleet(self, question: str, *, actor_user_id: str, ip_address: str | None) -> CopilotAnswer:
        """The explicit "Ask about the whole fleet" path (Activity tab Phase 4b) - reached only
        via the caller-supplied fleet_scope flag, never guessed from the question text. Mirrors
        the single-database findings-first branch above, but evidence spans every active
        database (app.ai.investigation.gather_fleet_evidence) and there is no single
        database_id/incident_id to attach to the audit record."""
        bundle = await gather_fleet_evidence(self.db, redis=self.redis)
        tools_used = [s.tool_name for s in bundle.tool_statuses if s.called]

        messages = build_copilot_messages_fleet(question, bundle)
        try:
            content, model = await self.llm.complete(messages)
        except AIGatewayError as exc:
            await self.audit.record(
                action="ai.copilot_query",
                success=False,
                actor_user_id=actor_user_id,
                target_type=None,
                target_id=None,
                ip_address=ip_address,
                metadata={"tools_used": tools_used, "fleet_scope": True, "question_preview": question[:200]},
            )
            raise UpstreamUnavailableError("The AI gateway is currently unavailable.") from exc

        await self.audit.record(
            action="ai.copilot_query",
            success=True,
            actor_user_id=actor_user_id,
            target_type=None,
            target_id=None,
            ip_address=ip_address,
            metadata={
                "tools_used": tools_used,
                "model": model,
                "fleet_scope": True,
                "question_preview": question[:200],
                "evidence_coverage": bundle.coverage.coverage_label,
            },
        )
        answer = f"{render_findings_as_facts(bundle.findings)}\n\n{content.strip()}" if bundle.findings else content

        return CopilotAnswer(
            answer=answer,
            model=model,
            tools_used=tools_used,
            tool_statuses=bundle.tool_statuses,
            evidence_coverage=bundle.coverage.coverage_label,
            findings=bundle.findings,
        )
