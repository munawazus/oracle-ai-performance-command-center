"""Pure computation of current metric values from raw telemetry rows - no I/O. Accepts
anything with .wait_class/.total_waits/.time_waited_centisecs (duck-typed so both real
WaitSnapshot rows and plain test doubles work identically).
"""

from dataclasses import dataclass
from typing import Protocol


class WaitRow(Protocol):
    wait_class: str | None
    total_waits: int
    time_waited_centisecs: int


@dataclass(frozen=True)
class WaitClassTotals:
    wait_class: str
    total_waits: int
    time_waited_centisecs: int


def aggregate_by_wait_class(rows: list[WaitRow]) -> dict[str, WaitClassTotals]:
    totals: dict[str, WaitClassTotals] = {}
    for row in rows:
        wc = row.wait_class or "Unclassified"
        existing = totals.get(wc)
        if existing is None:
            totals[wc] = WaitClassTotals(wc, row.total_waits, row.time_waited_centisecs)
        else:
            totals[wc] = WaitClassTotals(
                wc,
                existing.total_waits + row.total_waits,
                existing.time_waited_centisecs + row.time_waited_centisecs,
            )
    return totals


def compute_wait_class_rates(
    previous: dict[str, WaitClassTotals], current: dict[str, WaitClassTotals], elapsed_seconds: float
) -> dict[str, float]:
    """Centiseconds waited per second, per wait class, between two rounds of raw cumulative
    V$SYSTEM_EVENT counters. A class with no prior round (new since last sample) contributes
    no rate this round - there's nothing to diff against yet. A negative delta (the counter
    went backwards - the instance bounced) is dropped for the same reason, not clamped to 0,
    since 0 would misleadingly assert "no waits" rather than "unknown, instance restarted".
    """
    if elapsed_seconds <= 0:
        return {}

    rates: dict[str, float] = {}
    for wait_class, cur in current.items():
        prev = previous.get(wait_class)
        if prev is None:
            continue
        delta = cur.time_waited_centisecs - prev.time_waited_centisecs
        if delta < 0:
            continue
        rates[wait_class] = delta / elapsed_seconds
    return rates
