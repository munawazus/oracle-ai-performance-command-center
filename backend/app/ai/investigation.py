"""The shared evidence-governance layer for both AI features (CopilotService, RcaService).

Neither service gathers evidence on its own any more - both call gather_evidence() here so
evidence integrity (never mix databases), transparency (called vs found vs empty), freshness,
and deterministic correlation exist in exactly one place. app/ai/tools.py stays untouched: it
is a stable, narrow, single-repository fetch layer, and correlation that combines two tools'
outputs (e.g. blocking_chain + active_sessions) doesn't belong inside either individual tool.

The correlators below turn raw evidence into pre-digested Finding objects - concrete facts
(a blocker's SID, real hold duration, waiter count) computed with total accuracy in Python,
so the LLM's job becomes explaining/reasoning about already-established facts instead of
discovering them itself buried in a raw JSON dump. This exists because a real, scored
benchmark run (docs/RCA_BENCHMARK_REPORT.md) measured this platform's actual configured model
failing to reliably self-correlate from raw evidence, even though it never hallucinated.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import tools
from app.db import SessionLocal
from app.exceptions import ConflictError, NotFoundError
from app.logging_config import get_logger
from app.models.base import utcnow
from app.repositories.database_repo import MonitoredDatabaseRepository
from app.services.incident_service import SEVERITY_RANK

logger = get_logger(__name__)

_HIGH_SEVERITIES = {"HIGH", "CRITICAL"}

# How old a tool's own sampled_at can be before a Finding built from it is flagged stale.
# Per-category, not one blanket number: live session/wait telemetry (~15s collector tick)
# is stale in minutes; capacity/tablespace data changes slowly and is fine for hours.
_STALE_AFTER = {
    "active_sessions": timedelta(minutes=5),
    "blocking_chain": timedelta(minutes=5),
    "wait_profile": timedelta(minutes=5),
    "top_sql": timedelta(minutes=30),
    "sql_regressions": timedelta(hours=6),
}


class EvidenceScopeConflictError(ConflictError):
    """incident_id and database_id were both supplied and refer to different databases - a
    request that could otherwise silently mix one database's live telemetry with another
    database's incident. Rejected outright: an answer that looks like it addressed the
    request while actually mixing scopes is worse than a loud error."""


@dataclass(frozen=True)
class ToolCallStatus:
    tool_name: str
    called: bool
    returned_evidence: bool
    reason: str | None = None


@dataclass(frozen=True)
class Freshness:
    sampled_at: datetime
    age_seconds: float
    is_stale: bool


@dataclass(frozen=True)
class Finding:
    """One already-correlated fact, not a raw JSON blob - a category, a severity, a one-
    sentence summary naming concrete entities, and (where the source tool exposes one) the
    freshness of the evidence it was built from."""

    finding_id: str
    category: str
    severity: str  # matches IncidentSeverity: INFO | LOW | MEDIUM | HIGH | CRITICAL
    summary: str
    entities: dict = field(default_factory=dict)
    freshness: Freshness | None = None
    supporting_evidence_keys: list[str] = field(default_factory=list)


def format_age(age_seconds: float) -> str:
    """Same s/m/h/d bucketing as frontend/src/lib/time.ts's formatRelativeTime, kept in sync
    deliberately even though the two languages can't share the literal implementation."""
    if age_seconds < 60:
        return f"{int(age_seconds)}s"
    if age_seconds < 3600:
        return f"{int(age_seconds // 60)}m"
    if age_seconds < 86400:
        return f"{int(age_seconds // 3600)}h"
    return f"{int(age_seconds // 86400)}d"


def render_findings_as_facts(findings: list[Finding]) -> str:
    """A real, scored benchmark run (docs/RCA_BENCHMARK_REPORT.md) and this feature's own live
    QA both showed this platform's actual configured model restating a correct, pre-correlated
    finding as a WRONG number, or inventing a data source ("based on the AWR report") that was
    never in the evidence - even when explicitly instructed not to. Prompt wording alone isn't
    enough to guarantee this, so the facts a user actually sees are assembled here, verbatim,
    in Python - never trusted to the model to restate. The LLM's own contribution (see
    prompts.py) is appended after this block, never substituted for it."""
    lines = []
    for f in findings:
        if f.freshness is not None:
            age = f"sampled {format_age(f.freshness.age_seconds)} ago"
            if f.freshness.is_stale:
                age += " - this may be stale, treat with caution"
        else:
            age = "freshness unknown"
        lines.append(f"- [{f.severity}] {f.summary} ({age})")
    return "\n".join(lines)


@dataclass(frozen=True)
class EvidenceCoverage:
    categories_checked: int
    categories_with_evidence: int
    categories_empty: list[str]
    clear_cut_findings: int
    ambiguous: bool
    coverage_label: str  # "clear" | "partial" | "sparse"


@dataclass(frozen=True)
class EvidenceBundle:
    database_id: str | None
    incident_id: str | None
    raw_evidence: dict
    tool_statuses: list[ToolCallStatus]
    findings: list[Finding]
    coverage: EvidenceCoverage


def _freshness(sampled_at_iso: str | None, *, stale_after: timedelta) -> Freshness | None:
    if sampled_at_iso is None:
        return None
    sampled_at = datetime.fromisoformat(sampled_at_iso)
    now = utcnow()
    compare_now = now.replace(tzinfo=None) if sampled_at.tzinfo is None else now
    age_seconds = (compare_now - sampled_at).total_seconds()
    return Freshness(sampled_at=sampled_at, age_seconds=age_seconds, is_stale=age_seconds > stale_after.total_seconds())


async def _call(coro, tool_name: str, statuses: list[ToolCallStatus]) -> dict:
    result = await coro
    available = bool(result.get("available"))
    statuses.append(
        ToolCallStatus(
            tool_name=tool_name,
            called=True,
            returned_evidence=available,
            reason=None if available else result.get("reason"),
        )
    )
    return result


def _correlate_blocking(blocking_chain_evidence: dict, active_sessions_evidence: dict) -> list[Finding]:
    """One Finding per distinct blocker actually present in this sample - SID, real hold
    duration (V$SESSION.LAST_CALL_ET on the blocker's own session row), and how many sessions
    it's blocking. This is the exact fact pattern ("SID 421 has held a transaction for 812s
    and is blocking 37 sessions") a DBA needs, computed with total accuracy - not left for the
    model to notice buried in two separate raw evidence dicts."""
    if not blocking_chain_evidence.get("available"):
        return []
    chains = blocking_chain_evidence.get("blocking_chains") or []
    if not chains:
        return []

    sessions_by_sid = {}
    if active_sessions_evidence.get("available"):
        sessions_by_sid = {s["sid"]: s for s in active_sessions_evidence.get("sessions", [])}

    by_blocker: dict[int, list[dict]] = {}
    for chain in chains:
        by_blocker.setdefault(chain["blocker_sid"], []).append(chain)

    freshness = _freshness(blocking_chain_evidence.get("sampled_at"), stale_after=_STALE_AFTER["blocking_chain"])

    findings: list[Finding] = []
    for blocker_sid, waiters in by_blocker.items():
        if not waiters[0].get("blocker_in_this_sample"):
            continue
        blocker_session = sessions_by_sid.get(blocker_sid)
        hold_seconds = blocker_session.get("last_call_et") if blocker_session else None
        waiter_session = sessions_by_sid.get(waiters[0]["blocked_sid"])
        wait_event = waiter_session.get("event") if waiter_session else None
        waiter_count = len(waiters)

        hold_phrase = f"for {hold_seconds}s" if hold_seconds is not None else "for an unknown duration"
        event_phrase = f" on {wait_event}" if wait_event else ""
        severity = "CRITICAL" if waiter_count >= 5 or (hold_seconds or 0) >= 300 else "HIGH"

        entities: dict = {"blocker_sid": blocker_sid, "waiter_count": waiter_count}
        if blocker_session and blocker_session.get("username"):
            entities["blocker_username"] = blocker_session["username"]

        findings.append(
            Finding(
                finding_id=f"blocking_{blocker_sid}",
                category="blocking",
                severity=severity,
                summary=(
                    f"SID {blocker_sid} has been holding a transaction {hold_phrase} and is "
                    f"blocking {waiter_count} session(s){event_phrase}."
                ),
                entities=entities,
                freshness=freshness,
                supporting_evidence_keys=["blocking_chain", "active_sessions"],
            )
        )
    return findings


def _correlate_sql_regression(sql_regressions_evidence: dict) -> list[Finding]:
    if not sql_regressions_evidence.get("available"):
        return []
    findings = []
    for r in sql_regressions_evidence.get("regressions", []):
        ratio = r.get("ratio")
        ratio_phrase = f"{ratio:.1f}x" if isinstance(ratio, (int, float)) else "an unspecified factor"
        findings.append(
            Finding(
                finding_id=f"sql_regression_{r['sql_id']}",
                category="sql_regression",
                severity=r.get("severity") or "MEDIUM",
                summary=(
                    f"SQL {r['sql_id']} has an open {r['regression_kind']} regression "
                    f"({ratio_phrase} change), first detected {r['first_detected_at']}."
                ),
                entities={"sql_id": r["sql_id"], "regression_kind": r["regression_kind"]},
                freshness=_freshness(r.get("last_detected_at"), stale_after=_STALE_AFTER["sql_regressions"]),
                supporting_evidence_keys=["sql_regressions"],
            )
        )
    return findings


def _correlate_capacity(capacity_evidence: dict) -> list[Finding]:
    """get_tablespace_trend's all-tablespaces shape carries no top-level sampled_at, so these
    findings honestly carry no freshness rather than a fabricated one."""
    if not capacity_evidence.get("available"):
        return []
    findings = []
    for ts in capacity_evidence.get("tablespaces", []):
        severity = ts.get("open_alert_severity")
        if not severity:
            continue
        findings.append(
            Finding(
                finding_id=f"capacity_{ts['tablespace_name']}",
                category="capacity",
                severity=severity,
                summary=(
                    f"Tablespace {ts['tablespace_name']} is {ts['pct_used']}% used with an "
                    f"open {severity} capacity alert."
                ),
                entities={"tablespace_name": ts["tablespace_name"], "pct_used": ts["pct_used"]},
                freshness=None,
                supporting_evidence_keys=["capacity"],
            )
        )
    return findings


def _correlate_waits(wait_profile_evidence: dict) -> list[Finding]:
    if not wait_profile_evidence.get("available"):
        return []
    wait_classes = wait_profile_evidence.get("wait_classes") or []
    if not wait_classes or wait_classes[0]["centisec_waited_per_sec"] <= 0:
        return []
    top = wait_classes[0]
    return [
        Finding(
            finding_id="wait_profile_top",
            category="wait_profile",
            severity="INFO",
            summary=f"The dominant wait class right now is {top['wait_class']} at {top['centisec_waited_per_sec']} centisec/sec.",
            entities={"wait_class": top["wait_class"]},
            freshness=_freshness(wait_profile_evidence.get("sampled_at"), stale_after=_STALE_AFTER["wait_profile"]),
            supporting_evidence_keys=["wait_profile"],
        )
    ]


def _compute_coverage(tool_statuses: list[ToolCallStatus], findings: list[Finding]) -> EvidenceCoverage:
    checked = sum(1 for s in tool_statuses if s.called)
    with_evidence = sum(1 for s in tool_statuses if s.returned_evidence)
    empty = [s.tool_name for s in tool_statuses if s.called and not s.returned_evidence]
    clear_cut = [f for f in findings if f.severity in _HIGH_SEVERITIES]
    distinct_categories = {f.category for f in clear_cut}

    if not clear_cut:
        label = "sparse"
    elif len(distinct_categories) >= 2:
        label = "partial"
    else:
        label = "clear"

    return EvidenceCoverage(
        categories_checked=checked,
        categories_with_evidence=with_evidence,
        categories_empty=empty,
        clear_cut_findings=len(clear_cut),
        ambiguous=len(distinct_categories) >= 2,
        coverage_label=label,
    )


async def gather_evidence(
    db: AsyncSession, *, database_id: str | None, incident_id: str | None
) -> EvidenceBundle:
    """The single evidence-gathering entry point for both CopilotService and RcaService.

    Raises NotFoundError if incident_id is given but doesn't resolve to a real incident
    (matching RcaService's existing, stricter behavior - asking about a nonexistent incident
    should be a clear 404, not a silent "not much to say" answer). Raises
    EvidenceScopeConflictError if both incident_id and database_id are given and the incident
    belongs to a different database than the one supplied - never silently prefers one."""
    raw_evidence: dict = {}
    tool_statuses: list[ToolCallStatus] = []

    if incident_id:
        incident_evidence = await tools.get_incident(db, incident_id)
        if not incident_evidence.get("available"):
            raise NotFoundError("Incident not found.")
        derived_db_id = incident_evidence["incident"]["monitored_database_id"]
        if database_id and database_id != derived_db_id:
            raise EvidenceScopeConflictError(
                f"incident_id {incident_id} belongs to database {derived_db_id}, not the "
                f"supplied database_id {database_id}."
            )
        database_id = derived_db_id
        raw_evidence["incident"] = incident_evidence
        tool_statuses.append(ToolCallStatus("get_incident", called=True, returned_evidence=True))

    findings: list[Finding] = []
    if database_id:
        raw_evidence["database_health"] = await _call(
            tools.get_database_health(db, database_id), "get_database_health", tool_statuses
        )
        raw_evidence["active_sessions"] = await _call(
            tools.get_active_sessions(db, database_id), "get_active_sessions", tool_statuses
        )
        raw_evidence["wait_profile"] = await _call(
            tools.get_wait_profile(db, database_id), "get_wait_profile", tool_statuses
        )
        raw_evidence["blocking_chain"] = await _call(
            tools.get_blocking_chain(db, database_id), "get_blocking_chain", tool_statuses
        )
        raw_evidence["top_sql"] = await _call(tools.get_top_sql(db, database_id), "get_top_sql", tool_statuses)
        raw_evidence["sql_regressions"] = await _call(
            tools.get_sql_regressions(db, database_id), "get_sql_regressions", tool_statuses
        )
        raw_evidence["capacity"] = await _call(
            tools.get_tablespace_trend(db, database_id), "get_tablespace_trend", tool_statuses
        )

        findings = (
            _correlate_blocking(raw_evidence["blocking_chain"], raw_evidence["active_sessions"])
            + _correlate_sql_regression(raw_evidence["sql_regressions"])
            + _correlate_capacity(raw_evidence["capacity"])
            + _correlate_waits(raw_evidence["wait_profile"])
        )
        findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)

    coverage = _compute_coverage(tool_statuses, findings)
    return EvidenceBundle(
        database_id=database_id,
        incident_id=incident_id,
        raw_evidence=raw_evidence,
        tool_statuses=tool_statuses,
        findings=findings,
        coverage=coverage,
    )


# -- Fleet-wide evidence (Activity tab Phase 4a: AI Copilot "ask about the whole fleet") -----

# The 5 correlation-feeding tools _correlate_* actually use - deliberately excludes
# get_database_health/get_top_sql (raw-evidence-only, never correlated into a Finding) since a
# fleet-wide bundle never carries a raw_evidence dump at all (N databases' worth of raw JSON
# would blow the prompt budget - see build_copilot_messages_fleet in prompts.py).
_FLEET_TOOL_NAMES = (
    "get_active_sessions",
    "get_blocking_chain",
    "get_wait_profile",
    "get_sql_regressions",
    "get_tablespace_trend",
)

# Bounds concurrent per-database fleet-evidence gathering, same reasoning and same bound as
# app/services/activity_service.py's fleet_vitals (see that module's docstring for why each
# concurrent task needs its own SessionLocal(), not the caller's session).
_FLEET_MAX_CONCURRENT_DATABASES = 10

# A single fleet-wide snapshot, not per-user/per-filter - every asker sees the same "right now."
# Versioned in the key so a future schema change can't silently deserialize a stale shape.
FLEET_EVIDENCE_CACHE_KEY = "activity:fleet_evidence:v1"
# Shorter than the Activity tab's own 15s "live" UI poll tier (frontend/src/pages/
# ActivityPage.tsx) - a Copilot answer should never be staler than what the dashboard itself
# already shows for the same moment.
FLEET_EVIDENCE_CACHE_TTL_SECONDS = 10


async def _findings_for_one_database(database_id: str, database_name: str) -> tuple[list[Finding], dict[str, bool]]:
    """Runs the same 5 correlation-feeding tool calls/functions gather_evidence() uses for a
    single database, in its OWN SessionLocal() - a concurrent per-database task must never
    share the caller's AsyncSession (see app/services/activity_service.py's module docstring;
    SQLAlchemy's AsyncSession is explicitly documented as not safe for concurrent tasks)."""
    async with SessionLocal() as db:
        statuses: list[ToolCallStatus] = []
        active_sessions = await _call(tools.get_active_sessions(db, database_id), "get_active_sessions", statuses)
        blocking_chain = await _call(tools.get_blocking_chain(db, database_id), "get_blocking_chain", statuses)
        wait_profile = await _call(tools.get_wait_profile(db, database_id), "get_wait_profile", statuses)
        sql_regressions = await _call(
            tools.get_sql_regressions(db, database_id), "get_sql_regressions", statuses
        )
        capacity = await _call(tools.get_tablespace_trend(db, database_id), "get_tablespace_trend", statuses)

    findings = (
        _correlate_blocking(blocking_chain, active_sessions)
        + _correlate_sql_regression(sql_regressions)
        + _correlate_capacity(capacity)
        + _correlate_waits(wait_profile)
    )
    tagged = [
        Finding(
            finding_id=f"{database_id}:{f.finding_id}",
            category=f.category,
            severity=f.severity,
            summary=f"{f.summary} (database: {database_name})",
            entities={**f.entities, "database_id": database_id, "database_name": database_name},
            freshness=f.freshness,
            supporting_evidence_keys=f.supporting_evidence_keys,
        )
        for f in findings
    ]
    returned = {s.tool_name: s.returned_evidence for s in statuses}
    return tagged, returned


async def _gather_fleet_evidence_uncached(db: AsyncSession) -> EvidenceBundle:
    database_repo = MonitoredDatabaseRepository(db)
    active_ids = await database_repo.list_active_ids()
    if not active_ids:
        return EvidenceBundle(
            database_id=None, incident_id=None, raw_evidence={}, tool_statuses=[], findings=[],
            coverage=_compute_coverage([], []),
        )
    databases = await database_repo.list_all(database_ids=active_ids, limit=len(active_ids))

    semaphore = asyncio.Semaphore(_FLEET_MAX_CONCURRENT_DATABASES)

    async def _bounded(database) -> tuple[list[Finding], dict[str, bool]]:
        async with semaphore:
            return await _findings_for_one_database(database.id, database.name)

    results = await asyncio.gather(*[_bounded(d) for d in databases])

    all_findings: list[Finding] = []
    tool_returned_any: dict[str, bool] = dict.fromkeys(_FLEET_TOOL_NAMES, False)
    for findings, returned in results:
        all_findings.extend(findings)
        for name, got_evidence in returned.items():
            if got_evidence:
                tool_returned_any[name] = True

    all_findings.sort(key=lambda f: SEVERITY_RANK.get(f.severity, 0), reverse=True)
    tool_statuses = [
        ToolCallStatus(
            tool_name=name,
            called=True,
            returned_evidence=found_any,
            reason=None if found_any else f"no evidence found across {len(databases)} active database(s)",
        )
        for name, found_any in tool_returned_any.items()
    ]
    coverage = _compute_coverage(tool_statuses, all_findings)
    return EvidenceBundle(
        database_id=None, incident_id=None, raw_evidence={}, tool_statuses=tool_statuses,
        findings=all_findings, coverage=coverage,
    )


def _serialize_fleet_bundle(bundle: EvidenceBundle) -> str:
    def _default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"not JSON serializable: {obj!r}")

    return json.dumps(
        {
            "tool_statuses": [asdict(s) for s in bundle.tool_statuses],
            "findings": [asdict(f) for f in bundle.findings],
            "coverage": asdict(bundle.coverage),
        },
        default=_default,
    )


def _deserialize_fleet_bundle(raw: str) -> EvidenceBundle:
    data = json.loads(raw)
    tool_statuses = [ToolCallStatus(**s) for s in data["tool_statuses"]]
    findings = []
    for f in data["findings"]:
        freshness = None
        if f.get("freshness"):
            fr = f["freshness"]
            freshness = Freshness(
                sampled_at=datetime.fromisoformat(fr["sampled_at"]), age_seconds=fr["age_seconds"],
                is_stale=fr["is_stale"],
            )
        findings.append(
            Finding(
                finding_id=f["finding_id"], category=f["category"], severity=f["severity"],
                summary=f["summary"], entities=f["entities"], freshness=freshness,
                supporting_evidence_keys=f["supporting_evidence_keys"],
            )
        )
    coverage = EvidenceCoverage(**data["coverage"])
    return EvidenceBundle(
        database_id=None, incident_id=None, raw_evidence={}, tool_statuses=tool_statuses,
        findings=findings, coverage=coverage,
    )


async def gather_fleet_evidence(db: AsyncSession, *, redis: Redis | None = None) -> EvidenceBundle:
    """The fleet-wide counterpart to gather_evidence() - a real, deterministic evidence bundle
    for a general Copilot question asked about the whole fleet rather than one database (the
    explicit "Ask about the whole fleet" toggle, Activity tab Phase 4b -
    CopilotService.ask()'s fleet_scope branch). Reuses the exact same per-tool fetch layer and
    _correlate_* functions gather_evidence() uses, run per active database concurrently, then
    merges every database's Findings into one list, each tagged with which database it came
    from (Finding.entities' database_id/database_name).

    `redis`, when given, is a short-TTL (FLEET_EVIDENCE_CACHE_TTL_SECONDS) cache for this
    genuinely expensive computation (N databases x 5 tool calls each) - this platform's first
    "cache an expensive computation" pattern (Redis elsewhere is rate-limiting/session only).
    Fails open on any Redis error, same convention as RateLimitService's callers
    (app/security/deps.py, app/security/middleware.py) - a cache outage must degrade to
    "compute it fresh," never to a broken Copilot."""
    if redis is not None:
        try:
            cached = await redis.get(FLEET_EVIDENCE_CACHE_KEY)
        except Exception:
            logger.error("fleet_evidence_cache_read_failed_falling_back_to_uncached")
            cached = None
        if cached is not None:
            return _deserialize_fleet_bundle(cached)

    bundle = await _gather_fleet_evidence_uncached(db)

    if redis is not None:
        try:
            await redis.set(FLEET_EVIDENCE_CACHE_KEY, _serialize_fleet_bundle(bundle), ex=FLEET_EVIDENCE_CACHE_TTL_SECONDS)
        except Exception:
            logger.error("fleet_evidence_cache_write_failed_skipping_cache")

    return bundle
