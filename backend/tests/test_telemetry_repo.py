from app.repositories.telemetry_repo import _even_sample


def test_even_sample_returns_everything_when_within_limit():
    rows = [5, 4, 3, 2, 1]
    assert _even_sample(rows, limit=10) == rows
    assert _even_sample(rows, limit=5) == rows


def test_even_sample_keeps_newest_and_oldest_when_over_limit():
    rows_desc = list(range(100, 0, -1))  # 100 (newest) down to 1 (oldest)
    sampled = _even_sample(rows_desc, limit=10)
    assert sampled[0] == 100
    assert sampled[-1] == 1
    assert len(sampled) <= 10


def test_even_sample_spans_the_whole_range_not_just_the_recent_tail():
    # This is the exact bug this helper exists to fix: naively keeping the first `limit`
    # entries of a most-recent-first list would silently drop everything past the tail.
    rows_desc = list(range(1000, 0, -1))
    sampled = _even_sample(rows_desc, limit=100)
    assert min(sampled) < 50  # some entries come from deep in the "old" end
    assert max(sampled) == 1000  # newest is always kept


def test_even_sample_limit_of_one_keeps_only_the_newest():
    rows_desc = [30, 20, 10]
    assert _even_sample(rows_desc, limit=1) == [30]


def test_even_sample_empty_input():
    assert _even_sample([], limit=10) == []
