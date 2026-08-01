from datetime import datetime
from pathlib import Path

import pytest

from logs_processor.filtering import any_of, contains
from logs_processor.grouping import timestamp_block_grouper
from logs_processor.processor import LogProcessor
from logs_processor.timestamps import TIMESTAMP_DETECTION_LINES
from logs_processor.transforms import replace_pattern

FIXTURES = Path(__file__).parent / "fixtures"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def read_lines(name: str) -> list[str]:
    with open(FIXTURES / name) as f:
        return list(f)


def test_add_grouping_single_predicate():
    lp = LogProcessor().add_grouping(lambda b, prev, cur: cur.startswith("  "))
    result = list(lp.process(["1\n", "  cont\n", "2\n"]))
    assert result == [["1\n", "  cont\n"], ["2\n"]]


def test_add_grouping_or_combination():
    lp = (
        LogProcessor()
        .add_grouping(lambda b, prev, cur: cur.startswith("A"))
        .add_grouping(lambda b, prev, cur: cur.startswith("B"))
    )
    result = list(lp.process(["1\n", "A cont\n", "B cont\n", "2\n"]))
    assert result == [["1\n", "A cont\n", "B cont\n"], ["2\n"]]


def test_add_grouping_none_added_default_one_line_per_block():
    lp = LogProcessor()
    result = list(lp.process(["a\n", "b\n"]))
    assert result == [["a\n"], ["b\n"]]


def test_add_filter_single_predicate():
    lp = LogProcessor().add_filter(lambda b: "keep" in b[0])
    result = list(lp.process(["keep\n", "drop\n"]))
    assert result == [["keep\n"]]


def test_filter_mode_default_is_any():
    lp = LogProcessor().add_filter(contains("ERROR")).add_filter(contains("payments"))
    assert lp.filter_mode == "any"
    result = list(lp.process(["ERROR only\n", "payments only\n", "neither\n"]))
    assert result == [["ERROR only\n"], ["payments only\n"]]


def test_filter_mode_all_requires_all_filters():
    lp = LogProcessor(filter_mode="all")
    lp.add_filter(contains("ERROR")).add_filter(contains("payments"))
    result = list(lp.process(["ERROR only\n", "ERROR in payments\n", "payments only\n"]))
    assert result == [["ERROR in payments\n"]]


def test_filter_mode_changed_after_filters_added_affects_process():
    lp = LogProcessor().add_filter(contains("ERROR")).add_filter(contains("payments"))
    lines = ["ERROR only\n", "payments only\n", "neither\n"]

    any_result = list(lp.process(lines))
    lp.filter_mode = "all"
    all_result = list(lp.process(lines))

    assert any_result != all_result
    assert any_result == [["ERROR only\n"], ["payments only\n"]]
    assert all_result == []


def test_filter_mode_setter_rejects_invalid_value():
    lp = LogProcessor()
    with pytest.raises(ValueError):
        lp.filter_mode = "xor"


def test_constructor_filter_mode_kwarg():
    lp = LogProcessor(filter_mode="all")
    assert lp.filter_mode == "all"


TIMED_LINES = [
    "2026-08-01 09:59:59 INFO before\n",
    "2026-08-01 10:00:00 INFO first window start\n",
    "2026-08-01 10:00:30 ERROR first window\n",
    "2026-08-01 12:00:00 ERROR between windows\n",
    "2026-08-01 18:00:10 ERROR second window\n",
    "2026-08-01 19:00:00 INFO after\n",
]


def test_add_time_range_single_window():
    lp = LogProcessor(timestamp_format=TS_FORMAT).add_time_range(
        "2026-08-01 10:00:00", "2026-08-01 10:01:00"
    )
    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 10:00:00 INFO first window start\n"],
        ["2026-08-01 10:00:30 ERROR first window\n"],
    ]


def test_add_time_range_multiple_windows_are_or_combined():
    lp = (
        LogProcessor(timestamp_format=TS_FORMAT)
        .add_time_range("2026-08-01 10:00:00", "2026-08-01 10:01:00")
        .add_time_range("2026-08-01 18:00:00", "2026-08-01 18:01:00")
    )
    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 10:00:00 INFO first window start\n"],
        ["2026-08-01 10:00:30 ERROR first window\n"],
        ["2026-08-01 18:00:10 ERROR second window\n"],
    ]


def test_add_time_range_open_ended():
    lp = LogProcessor(timestamp_format=TS_FORMAT).add_time_range(start="2026-08-01 18:00:00")
    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 18:00:10 ERROR second window\n"],
        ["2026-08-01 19:00:00 INFO after\n"],
    ]


def test_time_range_is_anded_with_filters_despite_any_filter_mode():
    lp = LogProcessor(timestamp_format=TS_FORMAT)
    lp.add_filter(contains("ERROR")).add_filter(contains("INFO"))
    lp.add_time_range("2026-08-01 10:00:00", "2026-08-01 10:01:00")

    assert lp.filter_mode == "any"
    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 10:00:00 INFO first window start\n"],
        ["2026-08-01 10:00:30 ERROR first window\n"],
    ]


def test_time_range_with_grouping_keeps_whole_block():
    lines = [
        "2026-08-01 10:00:00 ERROR boom\n",
        "    at Foo.bar(Foo.java:42)\n",
        "2026-08-01 12:00:00 ERROR later\n",
        "    at Baz.qux(Baz.java:7)\n",
    ]
    lp = (
        LogProcessor(timestamp_format=TS_FORMAT)
        .add_grouping(timestamp_block_grouper())
        .add_time_range("2026-08-01 10:00:00", "2026-08-01 11:00:00")
    )
    result = list(lp.process(lines))
    assert result == [["2026-08-01 10:00:00 ERROR boom\n", "    at Foo.bar(Foo.java:42)\n"]]


def test_add_time_range_accepts_datetime_bounds():
    lp = LogProcessor(timestamp_format=TS_FORMAT).add_time_range(
        datetime(2026, 8, 1, 10, 0, 0), datetime(2026, 8, 1, 10, 1, 0)
    )
    result = list(lp.process(TIMED_LINES))
    assert len(result) == 2


def test_add_time_range_requires_a_bound():
    lp = LogProcessor(timestamp_format=TS_FORMAT)
    with pytest.raises(ValueError, match="at least one of start/end"):
        lp.add_time_range()


def test_add_time_range_start_after_end_raises():
    lp = LogProcessor(timestamp_format=TS_FORMAT)
    with pytest.raises(ValueError, match="is after its end"):
        lp.add_time_range("2026-08-01 11:00:00", "2026-08-01 10:00:00")


def test_add_time_range_bound_not_matching_format_raises():
    lp = LogProcessor(timestamp_format=TS_FORMAT)
    with pytest.raises(ValueError, match="does not match timestamp_format"):
        lp.add_time_range("01/08/2026")


def test_timestamp_format_setter_rejects_unsupported_directive():
    lp = LogProcessor()
    with pytest.raises(ValueError, match="%Q"):
        lp.timestamp_format = "%Y %Q"


def test_timestamp_format_set_after_construction():
    lp = LogProcessor()
    lp.timestamp_format = TS_FORMAT
    lp.add_time_range("2026-08-01 10:00:00", "2026-08-01 10:01:00")
    assert len(list(lp.process(TIMED_LINES))) == 2


def test_timestamp_format_alone_changes_nothing():
    lp = LogProcessor(timestamp_format=TS_FORMAT)
    assert list(lp.process(TIMED_LINES)) == [[line] for line in TIMED_LINES]


def test_add_time_range_string_bounds_resolved_against_detected_format():
    lp = LogProcessor().add_time_range("2026-08-01 10:00:00", "2026-08-01 10:01:00")
    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 10:00:00 INFO first window start\n"],
        ["2026-08-01 10:00:30 ERROR first window\n"],
    ]


def test_add_time_range_bound_checked_later_when_format_unknown():
    lp = LogProcessor().add_time_range("01/08/2026")  # accepted, wrong shape
    with pytest.raises(ValueError, match="does not match timestamp_format"):
        list(lp.process(TIMED_LINES))


def test_timestamp_grouping_uses_configured_format():
    lines = [
        "01/Aug/2026:10:00:00 +0000 ERROR boom\n",
        "    at Foo.bar(Foo.java:42)\n",
        "01/Aug/2026:10:00:01 +0000 INFO next\n",
    ]
    lp = LogProcessor(timestamp_format="%d/%b/%Y:%H:%M:%S %z").add_grouping(
        timestamp_block_grouper()
    )
    assert list(lp.process(lines)) == [lines[0:2], lines[2:3]]


def test_timestamp_grouping_and_range_share_the_detected_format():
    lines = [
        "01/Aug/2026:09:00:00 +0000 INFO early\n",
        "01/Aug/2026:10:00:00 +0000 ERROR boom\n",
        "    at Foo.bar(Foo.java:42)\n",
        "01/Aug/2026:11:00:00 +0000 INFO late\n",
    ]
    lp = (
        LogProcessor()
        .add_grouping(timestamp_block_grouper())
        .add_time_range("01/Aug/2026:09:30:00 +0000", "01/Aug/2026:10:30:00 +0000")
    )
    assert list(lp.process(lines)) == [lines[1:3]]


def test_grouping_pattern_overrides_timestamp_format():
    lines = [
        "2026-08-01 10:00:00 [LOG] first\n",
        "2026-08-01 10:00:01 continuation despite its timestamp\n",
        "2026-08-01 10:00:02 [LOG] second\n",
    ]
    lp = LogProcessor(timestamp_format=TS_FORMAT).add_grouping(
        timestamp_block_grouper(pattern=r"\[LOG\]")
    )
    assert list(lp.process(lines)) == [lines[0:2], lines[2:3]]


def test_undetectable_timestamp_format_fails_during_processing():
    lp = LogProcessor().add_time_range("2026-08-01 10:00:00")
    with pytest.raises(ValueError, match="Could not detect the log's timestamp format"):
        list(lp.process(["no timestamps in this log at all\n"]))


def test_detection_only_reads_the_head_of_the_log():
    lines = TIMED_LINES + [f"2026-08-01 10:00:{i:02d} INFO filler\n" for i in range(30)]
    pulled: list[str] = []

    def counting_iter():
        for line in lines:
            pulled.append(line)
            yield line

    lp = LogProcessor().add_time_range("2026-08-01 10:00:00", "2026-08-01 10:01:00")
    gen = lp.process(counting_iter())
    next(gen)

    assert len(pulled) == TIMESTAMP_DETECTION_LINES < len(lines)


def test_no_detection_when_nothing_needs_timestamps():
    lp = LogProcessor().add_filter(contains("ERROR"))
    result = list(lp.process(["ERROR without any timestamp\n", "quiet line\n"]))
    assert result == [["ERROR without any timestamp\n"]]


def test_add_transformer_sequential_chaining():
    lp = (
        LogProcessor()
        .add_transformer(lambda b: [x.upper() for x in b])
        .add_transformer(lambda b: [x.replace("A", "X") for x in b])
    )
    result = list(lp.process(["a\n"]))
    assert result == [["X\n"]]


def test_add_filter_nested_any_of_all_of_user_example():
    lp = LogProcessor(filter_mode="all")
    lp.add_filter(any_of(contains("ERROR"), contains("WARN"))).add_filter(contains("payments"))

    result = list(
        lp.process(
            [
                "ERROR in payments\n",
                "WARN in payments\n",
                "ERROR only\n",
                "payments only\n",
            ]
        )
    )
    assert result == [["ERROR in payments\n"], ["WARN in payments\n"]]


def test_process_streaming_laziness_through_class():
    lines = [
        "BLOCK1 start\n",
        "  continuation A\n",
        "  continuation B\n",
        "BLOCK2 start\n",
        "  continuation C\n",
    ]
    pulled: list[str] = []

    def counting_iter():
        for line in lines:
            pulled.append(line)
            yield line

    lp = LogProcessor().add_grouping(lambda b, prev, cur: not cur.startswith("BLOCK"))
    gen = lp.process(counting_iter())
    first_block = next(gen)

    assert first_block == ["BLOCK1 start\n", "  continuation A\n", "  continuation B\n"]
    assert len(pulled) < len(lines)


def test_process_file_default_pass_through(tmp_path):
    input_path = tmp_path / "in.log"
    output_path = tmp_path / "out.log"
    input_path.write_text("a\nb\nc\n")

    LogProcessor().process_file(input_path, output_path)

    assert output_path.read_text() == "a\nb\nc\n"


def test_process_file_empty_file(tmp_path):
    input_path = tmp_path / "in.log"
    output_path = tmp_path / "out.log"
    input_path.write_text("")

    LogProcessor().process_file(input_path, output_path)

    assert output_path.read_text() == ""


def test_process_file_end_to_end(tmp_path):
    lines = read_lines("mixed_blocks.log")
    input_path = tmp_path / "in.log"
    output_path = tmp_path / "out.log"
    input_path.write_text("".join(lines))

    lp = (
        LogProcessor()
        .add_grouping(timestamp_block_grouper())
        .add_filter(lambda b: not contains("DEBUG")(b))
        .add_transformer(replace_pattern([r"Payment"], replacement="[SVC]"))
    )
    lp.process_file(input_path, output_path)

    output = output_path.read_text()
    assert "DEBUG" not in output
    assert "[SVC]" in output
    assert "Payment" not in output


def test_from_config_builds_matching_pipeline():
    lp = LogProcessor.from_config(FIXTURES / "sample_config.yaml")
    assert lp.filter_mode == "all"

    lines = [
        "2026-08-01 10:00:00 ERROR payments failed for user a@b.com from 10.0.0.5\n",
        "2026-08-01 10:00:01 ERROR only\n",
        "2026-08-01 10:00:02 INFO payments only\n",
    ]
    result = list(lp.process(lines))

    assert result == [
        ["2026-08-01 10:00:00 ERROR payments failed for user [EMAIL] from 10.0.0.5\n"]
    ]


def test_from_config_default_filter_mode_when_absent(tmp_path):
    config_path = tmp_path / "minimal.yaml"
    config_path.write_text(
        "filters:\n"
        "  - type: contains\n"
        "    substring: ERROR\n"
    )

    lp = LogProcessor.from_config(config_path)

    assert lp.filter_mode == "any"


def test_from_config_empty_file_is_pass_through(tmp_path):
    config_path = tmp_path / "empty.yaml"
    config_path.write_text("")

    lp = LogProcessor.from_config(config_path)

    assert list(lp.process(["a\n", "b\n"])) == [["a\n"], ["b\n"]]


def test_from_config_unknown_filter_type_raises(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        "filters:\n"
        "  - type: not_a_real_filter\n"
    )

    with pytest.raises(ValueError, match="not_a_real_filter"):
        LogProcessor.from_config(config_path)


def test_from_config_time_ranges():
    lp = LogProcessor.from_config(FIXTURES / "time_ranges_config.yaml")
    assert lp.timestamp_format == TS_FORMAT

    result = list(lp.process(TIMED_LINES))
    assert result == [
        ["2026-08-01 10:00:30 ERROR first window\n"],
        ["2026-08-01 18:00:10 ERROR second window\n"],
    ]


def test_from_config_without_timestamp_format_detects_it(tmp_path):
    config_path = tmp_path / "no_format.yaml"
    config_path.write_text(
        "groupings:\n"
        "  - type: timestamp\n"
        "time_ranges:\n"
        "  - start: '2026-08-01 10:00:00'\n"
        "    end: '2026-08-01 10:01:00'\n"
    )

    lp = LogProcessor.from_config(config_path)

    assert lp.timestamp_format is None
    assert len(list(lp.process(TIMED_LINES))) == 2


def test_from_config_time_ranges_unknown_key_raises(tmp_path):
    config_path = tmp_path / "bad_range.yaml"
    config_path.write_text(
        "timestamp_format: '%Y-%m-%d %H:%M:%S'\n"
        "time_ranges:\n"
        "  - from: '2026-08-01 10:00:00'\n"
    )

    with pytest.raises(ValueError, match="from"):
        LogProcessor.from_config(config_path)


def test_from_config_result_is_fully_mutable(tmp_path):
    config_path = tmp_path / "minimal.yaml"
    config_path.write_text(
        "filters:\n"
        "  - type: contains\n"
        "    substring: KEEP\n"
    )

    lp = LogProcessor.from_config(config_path)
    lp.add_filter(contains("ALSO_KEEP"))

    result = list(lp.process(["KEEP this\n", "ALSO_KEEP this\n", "drop this\n"]))
    assert result == [["KEEP this\n"], ["ALSO_KEEP this\n"]]
