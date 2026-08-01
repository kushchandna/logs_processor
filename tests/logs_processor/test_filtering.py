import re
from datetime import datetime

import pytest

from logs_processor.filtering import (
    all_of,
    any_of,
    contains,
    ends_with,
    equal_to,
    filter_blocks,
    matches,
    starts_with,
    within_time_ranges,
)

TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def test_filter_blocks_keeps_true_drops_false():
    blocks = [["a"], ["b"], ["c"]]
    assert list(filter_blocks(blocks, lambda b: b[0] != "b")) == [["a"], ["c"]]


def test_filter_blocks_empty_input():
    assert list(filter_blocks([], lambda b: True)) == []


def test_all_of_requires_all_true():
    pred = all_of(lambda b: "a" in b, lambda b: "b" in b)
    assert pred(["a", "b"]) is True
    assert pred(["a"]) is False


def test_all_of_no_predicates_is_true():
    assert all_of()(["anything"]) is True


def test_any_of_true_if_any_true():
    pred = any_of(lambda b: "a" in b, lambda b: "b" in b)
    assert pred(["a"]) is True
    assert pred(["b"]) is True
    assert pred(["c"]) is False


def test_any_of_no_predicates_is_false():
    assert any_of()(["anything"]) is False


def test_all_of_any_of_nested_matches_error_or_warn_and_payments():
    pred = all_of(any_of(contains("ERROR"), contains("WARN")), contains("payments"))
    assert pred(["ERROR in payments service\n"]) is True
    assert pred(["WARN in payments service\n"]) is True
    assert pred(["ERROR only\n"]) is False
    assert pred(["payments only\n"]) is False


def test_equal_to_exact_match():
    assert equal_to("line2\n")(["line1\n", "line2\n"]) is True


def test_equal_to_no_match():
    assert equal_to("line2")(["line1\n", "line2\n"]) is False


def test_contains_substring_match():
    assert contains("err")(["some error here\n"]) is True
    assert contains("xyz")(["some error here\n"]) is False


def test_matches_string_pattern():
    assert matches(r"\d{3}")(["code 404\n"]) is True
    assert matches(r"\d{5}")(["code 404\n"]) is False


def test_matches_precompiled_pattern():
    assert matches(re.compile(r"^ERROR"))(["ERROR: boom\n"]) is True


def test_starts_with_prefix():
    assert starts_with("2026-")(["2026-08-01 x\n"]) is True
    assert starts_with("2027-")(["2026-08-01 x\n"]) is False


def test_ends_with_suffix():
    assert ends_with("done\n")(["task done\n"]) is True
    assert ends_with("done\n")(["task pending\n"]) is False


def line(stamp: str, text: str = "INFO something") -> str:
    return f"{stamp} {text}\n"


def test_within_time_ranges_single_range_inclusive_bounds():
    pred = within_time_ranges(
        [("2026-08-01 10:00:00", "2026-08-01 10:00:10")], TS_FORMAT
    )
    assert pred([line("2026-08-01 10:00:00")]) is True   # start is inclusive
    assert pred([line("2026-08-01 10:00:05")]) is True
    assert pred([line("2026-08-01 10:00:10")]) is True   # end is inclusive
    assert pred([line("2026-08-01 09:59:59")]) is False
    assert pred([line("2026-08-01 10:00:11")]) is False


def test_within_time_ranges_multiple_ranges_are_or_combined():
    pred = within_time_ranges(
        [
            ("2026-08-01 10:00:00", "2026-08-01 10:01:00"),
            ("2026-08-01 18:00:00", "2026-08-01 18:01:00"),
        ],
        TS_FORMAT,
    )
    assert pred([line("2026-08-01 10:00:30")]) is True
    assert pred([line("2026-08-01 18:00:30")]) is True
    assert pred([line("2026-08-01 14:00:00")]) is False


def test_within_time_ranges_open_ended_bounds():
    from_only = within_time_ranges([("2026-08-01 12:00:00", None)], TS_FORMAT)
    assert from_only([line("2026-08-01 23:59:59")]) is True
    assert from_only([line("2026-08-01 11:59:59")]) is False

    until_only = within_time_ranges([(None, "2026-08-01 12:00:00")], TS_FORMAT)
    assert until_only([line("2020-01-01 00:00:00")]) is True
    assert until_only([line("2026-08-01 12:00:01")]) is False


def test_within_time_ranges_accepts_datetime_bounds():
    pred = within_time_ranges(
        [(datetime(2026, 8, 1, 10, 0, 0), datetime(2026, 8, 1, 11, 0, 0))], TS_FORMAT
    )
    assert pred([line("2026-08-01 10:30:00")]) is True
    assert pred([line("2026-08-01 11:30:00")]) is False


def test_within_time_ranges_judges_block_by_its_first_timestamp():
    block = [
        line("2026-08-01 10:00:00", "ERROR boom"),
        "    at Foo.bar(Foo.java:42)\n",
        line("2026-08-01 23:00:00", "(quoted from later)"),
    ]
    pred = within_time_ranges([("2026-08-01 10:00:00", "2026-08-01 11:00:00")], TS_FORMAT)
    assert pred(block) is True


def test_within_time_ranges_untimestamped_block_dropped_by_default():
    pred = within_time_ranges([("2026-08-01 10:00:00", None)], TS_FORMAT)
    assert pred(["no timestamp here\n"]) is False


def test_within_time_ranges_keep_untimestamped_opt_in():
    pred = within_time_ranges(
        [("2026-08-01 10:00:00", None)], TS_FORMAT, keep_untimestamped=True
    )
    assert pred(["no timestamp here\n"]) is True
    assert pred([line("2026-08-01 09:00:00")]) is False


def test_within_time_ranges_no_ranges_matches_nothing():
    assert within_time_ranges([], TS_FORMAT)([line("2026-08-01 10:00:00")]) is False


def test_within_time_ranges_start_after_end_raises():
    with pytest.raises(ValueError, match="is after its end"):
        within_time_ranges([("2026-08-01 11:00:00", "2026-08-01 10:00:00")], TS_FORMAT)
