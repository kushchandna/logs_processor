import warnings
from datetime import date, datetime

import pytest

from logs_processor.timestamps import (
    detect_timestamp_format,
    parse_timestamp,
    timestamp_extractor,
    timestamp_pattern,
)


def test_timestamp_pattern_matches_literals_and_directives():
    pattern = timestamp_pattern("%Y-%m-%d %H:%M:%S")
    assert pattern.search("2026-08-01 10:00:00").group(0) == "2026-08-01 10:00:00"


def test_timestamp_pattern_escapes_literal_characters():
    pattern = timestamp_pattern("[%H.%M]")
    assert pattern.search("[10.30]") is not None
    assert pattern.search("X10X30Y") is None


def test_timestamp_pattern_unsupported_directive_raises():
    with pytest.raises(ValueError, match="%Q"):
        timestamp_pattern("%Y %Q")


def test_extract_timestamp_at_start_of_line():
    extract = timestamp_extractor("%Y-%m-%d %H:%M:%S")
    assert extract("2026-08-01 10:00:00 INFO up\n") == datetime(2026, 8, 1, 10, 0, 0)


def test_extract_timestamp_mid_line():
    extract = timestamp_extractor("%Y-%m-%d %H:%M:%S")
    assert extract("INFO [2026-08-01 10:00:00] up\n") == datetime(2026, 8, 1, 10, 0, 0)


def test_extract_returns_none_for_line_without_timestamp():
    extract = timestamp_extractor("%Y-%m-%d %H:%M:%S")
    assert extract("    at Foo.bar(Foo.java:42)\n") is None


def test_extract_skips_candidate_rejected_by_strptime():
    extract = timestamp_extractor("%Y-%m-%d %H:%M:%S")
    line = "bad 2026-13-01 10:00:00 then 2026-08-01 11:30:00\n"
    assert extract(line) == datetime(2026, 8, 1, 11, 30, 0)


def test_extract_tolerates_space_padded_day():
    extract = timestamp_extractor("%Y %b %d %H:%M:%S")
    assert extract("2026 Aug  1 10:00:00 host sshd[1]: hi\n") == datetime(2026, 8, 1, 10)
    assert extract("2026 Aug 1 10:00:00 host sshd[1]: hi\n") == datetime(2026, 8, 1, 10)


def test_extract_year_less_format_pins_1900_without_warning():
    extract = timestamp_extractor("%b %d %H:%M:%S")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert extract("Aug  1 10:00:00 host sshd[1]: hi\n") == datetime(1900, 8, 1, 10)


def test_detect_iso_format():
    lines = [
        "2026-08-01T10:00:00 INFO up\n",
        "2026-08-01T10:00:01 INFO listening\n",
    ]
    assert detect_timestamp_format(lines) == "%Y-%m-%dT%H:%M:%S"


def test_detect_prefers_specific_format_over_looser_one():
    lines = ["2026-08-01 10:00:00,123 INFO up\n", "2026-08-01 10:00:01,456 INFO ok\n"]
    assert detect_timestamp_format(lines) == "%Y-%m-%d %H:%M:%S,%f"


def test_detect_logcat_style_month_day():
    lines = [
        "08-01 10:00:00.123  1234  5678 I Tag: hello\n",
        "08-01 10:00:01.456  1234  5678 W Tag: careful\n",
    ]
    assert detect_timestamp_format(lines) == "%m-%d %H:%M:%S.%f"


def test_detect_nginx_access_log():
    lines = ['1.2.3.4 - - [01/Aug/2026:10:00:00 +0000] "GET / HTTP/1.1" 200\n']
    assert detect_timestamp_format(lines) == "%d/%b/%Y:%H:%M:%S %z"


def test_detect_majority_wins_over_stray_timestamp_in_a_message():
    lines = [
        "08-01 10:00:00.123 I Tag: replaying 2026-07-01T00:00:00 snapshot\n",
        "08-01 10:00:01.456 I Tag: done\n",
        "08-01 10:00:02.789 I Tag: idle\n",
    ]
    assert detect_timestamp_format(lines) == "%m-%d %H:%M:%S.%f"


def test_detect_returns_none_without_timestamps():
    assert detect_timestamp_format(["nothing here\n", "or here\n"]) is None


def test_detect_returns_none_for_empty_input():
    assert detect_timestamp_format([]) is None


def test_detect_ignores_untimestamped_lines():
    lines = [
        "=== log begins ===\n",
        "    at Foo.bar(Foo.java:42)\n",
        "2026-08-01 10:00:00 INFO up\n",
    ]
    assert detect_timestamp_format(lines) == "%Y-%m-%d %H:%M:%S"


def test_parse_timestamp_passes_datetime_through():
    moment = datetime(2026, 8, 1, 10, 0, 0)
    assert parse_timestamp(moment, "%Y-%m-%d %H:%M:%S") is moment


def test_parse_timestamp_date_becomes_midnight():
    assert parse_timestamp(date(2026, 8, 1), "%Y-%m-%d %H:%M:%S") == datetime(2026, 8, 1)


def test_parse_timestamp_string_uses_format():
    assert parse_timestamp("2026-08-01 10:00:00", "%Y-%m-%d %H:%M:%S") == datetime(
        2026, 8, 1, 10, 0, 0
    )


def test_parse_timestamp_string_not_matching_format_raises():
    with pytest.raises(ValueError, match="does not match timestamp_format"):
        parse_timestamp("01/08/2026", "%Y-%m-%d %H:%M:%S")


def test_parse_timestamp_wrong_type_raises():
    with pytest.raises(TypeError):
        parse_timestamp(1754042400, "%Y-%m-%d %H:%M:%S")
