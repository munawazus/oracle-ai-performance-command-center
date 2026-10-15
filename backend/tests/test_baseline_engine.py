import math

from app.analytics.baseline_engine import (
    EMPTY_STATS,
    MIN_SAMPLES_FOR_ANOMALY,
    BaselineStats,
    classify_severity,
    welford_update,
    z_score,
)


def test_welford_matches_hand_computed_mean_and_stddev():
    values = [10.0, 12.0, 8.0, 14.0, 6.0]
    stats = EMPTY_STATS
    for v in values:
        stats = welford_update(stats, v)

    expected_mean = sum(values) / len(values)
    expected_variance = sum((v - expected_mean) ** 2 for v in values) / len(values)

    assert stats.sample_count == len(values)
    assert math.isclose(stats.mean, expected_mean)
    assert math.isclose(stats.variance, expected_variance)
    assert math.isclose(stats.stddev, math.sqrt(expected_variance))


def test_welford_is_order_independent():
    values = [3.0, 7.0, 1.0, 9.0, 5.0, 5.0, 5.0]
    forward = EMPTY_STATS
    for v in values:
        forward = welford_update(forward, v)

    backward = EMPTY_STATS
    for v in reversed(values):
        backward = welford_update(backward, v)

    assert math.isclose(forward.mean, backward.mean)
    assert math.isclose(forward.variance, backward.variance, abs_tol=1e-9)


def test_z_score_is_none_below_min_samples():
    stats = BaselineStats(sample_count=MIN_SAMPLES_FOR_ANOMALY - 1, mean=100.0, m2=50.0)
    assert z_score(stats, 500.0) is None


def test_z_score_is_none_when_no_variance_seen_yet():
    stats = BaselineStats(sample_count=MIN_SAMPLES_FOR_ANOMALY, mean=100.0, m2=0.0)
    assert z_score(stats, 500.0) is None


def test_z_score_matches_hand_computation():
    stats = BaselineStats(sample_count=MIN_SAMPLES_FOR_ANOMALY, mean=100.0, m2=100.0)  # variance=5, stddev~2.236
    z = z_score(stats, 100.0 + 2 * stats.stddev)
    assert z is not None
    assert math.isclose(z, 2.0, rel_tol=1e-9)


def test_classify_severity_thresholds():
    assert classify_severity(1.9) is None
    assert classify_severity(-1.9) is None
    assert classify_severity(2.0) == "LOW"
    assert classify_severity(3.0) == "MEDIUM"
    assert classify_severity(4.0) == "HIGH"
    assert classify_severity(6.0) == "CRITICAL"
    assert classify_severity(-6.5) == "CRITICAL"
