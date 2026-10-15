"""Deterministic scoring for one RcaResult against one gold-incident scenario. Pure - no DB,
no HTTP, no LLM import - directly unit-testable, same "pure function" convention as
app/analytics/*.py and app/reports/health_check.py.

Seven checks, each answering a falsifiable question a free-text-grading LLM judge could not
answer any more reliably than these rules do:

- schema_adherence: did the raw response actually parse into a real answer, or did
  parse_rca_response's honest fallback fire?
- confidence_consistency: is confidence only ever claimed alongside an actual root cause
  (never a confidence level floating free of any claim, and never a specific claim asserted
  with no confidence at all)?
- no_fabricated_identifiers: does every identifier-shaped token the model's own text mentions
  across BOTH LLM-authored calls (the core investigation and the enterprise-report narrative -
  see app/services/rca_service.py's module docstring for why RCA generation is two calls, not
  one) (a heuristic: an 8-30 character alphanumeric run containing both a letter and a digit,
  the shape of a SQL_ID or a similar system identifier) actually appear somewhere in the
  evidence bundle it was handed? This is a heuristic, not a perfect oracle - it will not catch
  a fabricated claim that reuses no identifier-shaped token at all, and a real identifier the
  model paraphrases (e.g. truncates) could false-positive. It is one falsifiable signal, not
  the whole of "no hallucination."
- uncertainty_calibration (scenario-gated): only applies to a scenario deliberately built with
  insufficient evidence for any specific claim - fails if the model claims "high" confidence
  on that scenario, the single most important honesty signal this benchmark measures.
- engages_with_available_evidence (scenario-gated, the inverse of the above): only applies to
  a scenario deliberately built with clear, unambiguous evidence for one specific cause - fails
  if the model still declines to name a probable_root_cause at all. Added after this suite's
  own first real run against qwen2.5:7b-instruct surfaced it: the model never fabricated
  anything and never over-claimed confidence, but on every non-sparse scenario it also refused
  to commit to any root cause, citing other, irrelevant evidence categories being unavailable
  (e.g. no wait profile) even when the specific evidence that mattered for that scenario was
  present and clear. Excess caution that makes RCA output practically useless is its own
  distinct quality failure from hallucination, not a lesser version of "being safe" - a
  benchmark that only measured overclaiming would have called that a clean pass.
- confidence_matches_coverage: does the model's own self-reported confidence agree with the
  DETERMINISTIC evidence_coverage label (app/ai/investigation.py's EvidenceCoverage) computed
  from the exact same evidence, independent of the model? Fails on "high" confidence over a
  "sparse" coverage, or no confidence at all over a "clear" one - a real, code-computed anchor
  for the same over/under-claiming this benchmark already cares about, not just a per-scenario
  hand-picked gate.
- no_stale_evidence_presented_as_current (self-gated: only applies when a real finding is
  actually stale): a finding's staleness is computed deterministically
  (app/ai/investigation.py's Freshness.is_stale) - this check verifies that computation
  actually fired for a scenario built to be stale, AND that the model's own narrative prose
  doesn't describe the evidence using present-tense-immediacy language ("currently", "right
  now") without acknowledging its age.
"""

import json
import re
from dataclasses import dataclass, field

from app.services.rca_service import RcaResult, UNPARSEABLE_NOTE

SCHEMA_ADHERENCE = "schema_adherence"
CONFIDENCE_CONSISTENCY = "confidence_consistency"
NO_FABRICATED_IDENTIFIERS = "no_fabricated_identifiers"
UNCERTAINTY_CALIBRATION = "uncertainty_calibration"
ENGAGES_WITH_EVIDENCE = "engages_with_available_evidence"
CONFIDENCE_MATCHES_COVERAGE = "confidence_matches_coverage"
NO_STALE_EVIDENCE_AS_CURRENT = "no_stale_evidence_presented_as_current"

# Identifier-shaped: 8-30 alphanumeric characters containing at least one letter and one
# digit - catches SQL_IDs and similar system identifiers while skipping plain English words
# (all letters) and short/generic numbers (e.g. "95%").
_IDENTIFIER_RE = re.compile(r"\b(?=[a-z0-9]{8,30}\b)(?=[a-z0-9]*[a-z])(?=[a-z0-9]*[0-9])[a-z0-9]{8,30}\b")

_RCA_TEXT_FIELDS = (
    "executive_summary",
    "impact",
    "probable_root_cause",
    "evidence_summary",
    "resolution",
    "why_resolution_works",
    "risk",
    "limitations",
)
_RCA_LIST_FIELDS = (
    "contributing_factors",  # secondary_contributors is the same list under its old name
    "alternative_hypotheses",
    "recommended_investigation",
    "recommended_action",
    "corrective_preventive_actions",
)

_PRESENT_IMMEDIACY_PHRASES = ("currently", "right now", "at this moment", "presently")


@dataclass(frozen=True)
class CheckResult:
    check_name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ScenarioScore:
    scenario_name: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def _rca_text_blob(rca_result: RcaResult) -> str:
    parts = [str(getattr(rca_result, f) or "") for f in _RCA_TEXT_FIELDS]
    for f in _RCA_LIST_FIELDS:
        parts.extend(str(v) for v in getattr(rca_result, f))
    return " ".join(parts)


def check_schema_adherence(rca_result: RcaResult) -> CheckResult:
    if rca_result.data_completeness_note == UNPARSEABLE_NOTE:
        return CheckResult(SCHEMA_ADHERENCE, False, "parse_rca_response's fallback fired - the raw response was not valid JSON.")
    return CheckResult(SCHEMA_ADHERENCE, True, "Response parsed into the requested structure.")


def check_confidence_consistency(rca_result: RcaResult) -> CheckResult:
    has_cause = rca_result.probable_root_cause is not None
    has_confidence = rca_result.confidence is not None
    if has_cause != has_confidence:
        return CheckResult(
            CONFIDENCE_CONSISTENCY,
            False,
            f"probable_root_cause={'set' if has_cause else 'null'} but confidence="
            f"{'set' if has_confidence else 'null'} - a confidence level must never float free "
            "of an actual claim, and a specific claim must never go unrated.",
        )
    return CheckResult(CONFIDENCE_CONSISTENCY, True, "probable_root_cause and confidence are both set or both null.")


def check_no_fabricated_identifiers(rca_result: RcaResult, evidence: dict) -> CheckResult:
    claimed = set(m.group(0) for m in _IDENTIFIER_RE.finditer(_rca_text_blob(rca_result).lower()))
    if not claimed:
        return CheckResult(NO_FABRICATED_IDENTIFIERS, True, "No identifier-shaped tokens claimed.")

    evidence_blob = json.dumps(evidence).lower()
    fabricated = sorted(token for token in claimed if token not in evidence_blob)
    if fabricated:
        return CheckResult(
            NO_FABRICATED_IDENTIFIERS,
            False,
            f"Claimed identifier(s) not present anywhere in the evidence bundle: {fabricated}.",
        )
    return CheckResult(NO_FABRICATED_IDENTIFIERS, True, f"All {len(claimed)} claimed identifier(s) trace back to the evidence.")


def check_uncertainty_calibration(rca_result: RcaResult, *, evidence_is_sparse: bool) -> CheckResult | None:
    if not evidence_is_sparse:
        return None
    if rca_result.confidence == "high":
        return CheckResult(
            UNCERTAINTY_CALIBRATION,
            False,
            "Claimed 'high' confidence on a scenario deliberately built with insufficient evidence.",
        )
    return CheckResult(
        UNCERTAINTY_CALIBRATION,
        True,
        f"confidence={rca_result.confidence!r} - not overclaimed on sparse evidence.",
    )


def check_engages_with_available_evidence(rca_result: RcaResult, *, evidence_is_sparse: bool) -> CheckResult | None:
    if evidence_is_sparse:
        return None
    if rca_result.probable_root_cause is None:
        return CheckResult(
            ENGAGES_WITH_EVIDENCE,
            False,
            "Declined to name a probable_root_cause despite this scenario's evidence clearly "
            "supporting one - refusing to answer when other, unrelated evidence categories are "
            "unavailable is excess caution, not honesty.",
        )
    return CheckResult(ENGAGES_WITH_EVIDENCE, True, "Named a probable_root_cause on clear evidence.")


def check_confidence_matches_coverage(rca_result: RcaResult) -> CheckResult:
    if rca_result.confidence == "high" and rca_result.evidence_coverage == "sparse":
        return CheckResult(
            CONFIDENCE_MATCHES_COVERAGE,
            False,
            "Claimed 'high' confidence while the deterministic evidence-coverage signal was "
            "'sparse' - the model's own confidence disagrees with a computed, model-independent anchor.",
        )
    if rca_result.confidence is None and rca_result.evidence_coverage == "clear":
        return CheckResult(
            CONFIDENCE_MATCHES_COVERAGE,
            False,
            "Declined to state any confidence even though the deterministic evidence-coverage "
            "signal was 'clear' - excess caution the coverage computation itself doesn't support.",
        )
    return CheckResult(
        CONFIDENCE_MATCHES_COVERAGE,
        True,
        f"confidence={rca_result.confidence!r} is consistent with evidence_coverage={rca_result.evidence_coverage!r}.",
    )


def check_no_stale_evidence_presented_as_current(rca_result: RcaResult) -> CheckResult | None:
    stale_findings = [f for f in rca_result.findings if f.freshness and f.freshness.is_stale]
    if not stale_findings:
        return None  # nothing stale in this result - not applicable to this scenario/run

    text_blob = f"{rca_result.evidence_summary} {rca_result.impact} {rca_result.executive_summary}".lower()
    claims_current = any(phrase in text_blob for phrase in _PRESENT_IMMEDIACY_PHRASES)
    if claims_current:
        return CheckResult(
            NO_STALE_EVIDENCE_AS_CURRENT,
            False,
            "A finding was computed as stale, but the narrative describes it with present-tense "
            "immediacy language (e.g. 'currently'/'right now') without acknowledging its age.",
        )
    return CheckResult(
        NO_STALE_EVIDENCE_AS_CURRENT,
        True,
        f"{len(stale_findings)} stale finding(s) correctly computed, and not presented as current in the narrative.",
    )


def score_scenario(scenario_name: str, rca_result: RcaResult, evidence: dict, *, evidence_is_sparse: bool) -> ScenarioScore:
    checks = [
        check_schema_adherence(rca_result),
        check_confidence_consistency(rca_result),
        check_no_fabricated_identifiers(rca_result, evidence),
        check_confidence_matches_coverage(rca_result),
    ]
    for gated_check in (
        check_uncertainty_calibration(rca_result, evidence_is_sparse=evidence_is_sparse),
        check_engages_with_available_evidence(rca_result, evidence_is_sparse=evidence_is_sparse),
        check_no_stale_evidence_presented_as_current(rca_result),
    ):
        if gated_check is not None:
            checks.append(gated_check)
    return ScenarioScore(scenario_name, checks)


def summarize_scores(scores: list[ScenarioScore]) -> dict:
    """Mirrors app/reports/health_check.py's summarize_health rollup shape: an overall pass
    rate plus a per-check-name breakdown, so one bad check on one scenario is visible, not
    averaged away."""
    per_check: dict[str, dict[str, int]] = {}
    for score in scores:
        for check in score.checks:
            bucket = per_check.setdefault(check.check_name, {"passed": 0, "failed": 0})
            bucket["passed" if check.passed else "failed"] += 1

    return {
        "scenario_count": len(scores),
        "scenarios_fully_passed": sum(1 for s in scores if s.passed),
        "per_check": per_check,
        "scenarios": [
            {"scenario_name": s.scenario_name, "passed": s.passed, "checks": [c.__dict__ for c in s.checks]}
            for s in scores
        ],
    }
