"""Pure, deterministic baselining math - no I/O, no Oracle, no DB. Rolling per-hour-of-day
mean/stddev via Welford's online algorithm (numerically stable, no need to store raw
history to recompute stats), plus a z-score-based anomaly classifier.

Tunable, not fixed forever: MIN_SAMPLES_FOR_ANOMALY and the z-score severity thresholds in
classify_severity are v1 defaults (master plan Sec 7 lists rolling/percentile/time-of-day
baselines as options - this implements rolling + time-of-day). Revisit once real telemetry
volume shows what actually separates noise from a real anomaly.
"""

import math
from dataclasses import dataclass

MIN_SAMPLES_FOR_ANOMALY = 20


@dataclass(frozen=True)
class BaselineStats:
    sample_count: int
    mean: float
    m2: float  # sum of squared differences from the running mean (Welford's algorithm)

    @property
    def variance(self) -> float:
        return self.m2 / self.sample_count if self.sample_count > 0 else 0.0

    @property
    def stddev(self) -> float:
        return math.sqrt(self.variance)


EMPTY_STATS = BaselineStats(sample_count=0, mean=0.0, m2=0.0)


def welford_update(stats: BaselineStats, observed: float) -> BaselineStats:
    count = stats.sample_count + 1
    delta = observed - stats.mean
    mean = stats.mean + delta / count
    delta2 = observed - mean
    m2 = stats.m2 + delta * delta2
    return BaselineStats(sample_count=count, mean=mean, m2=m2)


def z_score(stats: BaselineStats, observed: float) -> float | None:
    """None means "not enough evidence to judge" - too few samples, or zero variance seen
    so far - never a silent 0.0 that could misleadingly read as "definitely normal"."""
    if stats.sample_count < MIN_SAMPLES_FOR_ANOMALY or stats.stddev == 0:
        return None
    return (observed - stats.mean) / stats.stddev


def classify_severity(z: float) -> str | None:
    """None below 2 stddev - not flagged as an anomaly at all."""
    magnitude = abs(z)
    if magnitude >= 6:
        return "CRITICAL"
    if magnitude >= 4:
        return "HIGH"
    if magnitude >= 3:
        return "MEDIUM"
    if magnitude >= 2:
        return "LOW"
    return None
