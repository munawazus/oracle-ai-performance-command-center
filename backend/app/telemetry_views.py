"""Pure shaping of raw telemetry rows into what a caller actually wants - "the latest
collection round", "who's blocking whom" - shared by the AI tool layer (app/ai/tools.py)
and the dashboard API routes so the logic exists exactly once.
"""

from app.models.telemetry import SessionSample


def latest_round(samples: list[SessionSample]) -> list[SessionSample]:
    if not samples:
        return []
    latest_time = samples[0].sampled_at
    return [s for s in samples if s.sampled_at == latest_time]


def build_blocking_chains(session_round: list[SessionSample]) -> list[dict]:
    by_sid = {s.sid: s for s in session_round}
    blocked = [s for s in session_round if s.blocking_session is not None]

    chains = []
    for session in blocked:
        blocker_sid = session.blocking_session
        if blocker_sid is None:
            continue  # unreachable given the `blocked` filter above; satisfies mypy narrowing
        blocker = by_sid.get(blocker_sid)
        chains.append(
            {
                "blocked_sid": session.sid,
                "blocked_username": session.username,
                "blocked_sql_id": session.sql_id,
                "blocker_sid": blocker_sid,
                "blocker_username": blocker.username if blocker else None,
                "blocker_sql_id": blocker.sql_id if blocker else None,
                "blocker_in_this_sample": blocker is not None,
            }
        )
    return chains
