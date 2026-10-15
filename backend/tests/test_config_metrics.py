from app.analytics.config_metrics import (
    CHANGED,
    NEWLY_SET,
    REVERTED_TO_DEFAULT,
    ParameterChange,
    compute_parameter_changes,
)


class _Row:
    def __init__(self, parameter_name: str, parameter_value):
        self.parameter_name = parameter_name
        self.parameter_value = parameter_value


def test_no_changes_between_identical_rounds():
    previous = [_Row("open_cursors", "300")]
    current = [_Row("open_cursors", "300")]
    assert compute_parameter_changes(previous, current) == []


def test_newly_set_parameter():
    previous: list[_Row] = []
    current = [_Row("audit_trail", "DB")]
    changes = compute_parameter_changes(previous, current)
    assert changes == [ParameterChange("audit_trail", None, "DB", NEWLY_SET)]


def test_changed_parameter():
    previous = [_Row("open_cursors", "300")]
    current = [_Row("open_cursors", "500")]
    changes = compute_parameter_changes(previous, current)
    assert changes == [ParameterChange("open_cursors", "300", "500", CHANGED)]


def test_reverted_to_default_parameter():
    previous = [_Row("sga_target", "4000000000")]
    current: list[_Row] = []
    changes = compute_parameter_changes(previous, current)
    assert changes == [ParameterChange("sga_target", "4000000000", None, REVERTED_TO_DEFAULT)]


def test_mixed_changes_sorted_by_name():
    previous = [_Row("open_cursors", "300"), _Row("sga_target", "4000000000")]
    current = [_Row("open_cursors", "500"), _Row("audit_trail", "DB")]
    changes = compute_parameter_changes(previous, current)
    assert [c.parameter_name for c in changes] == ["audit_trail", "open_cursors", "sga_target"]
    assert [c.change_type for c in changes] == [NEWLY_SET, CHANGED, REVERTED_TO_DEFAULT]


def test_empty_rounds_produce_no_changes():
    assert compute_parameter_changes([], []) == []
