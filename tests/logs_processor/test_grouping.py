from pathlib import Path

import pytest

from logs_processor.grouping import (
    group_lines,
    indent_continuation_grouper,
    timestamp_block_grouper,
)

FIXTURES = Path(__file__).parent / "fixtures"


def read_lines(name: str) -> list[str]:
    with open(FIXTURES / name) as f:
        return list(f)


def test_group_lines_no_group_by_each_line_own_block():
    lines = ["a\n", "b\n", "c\n"]
    assert list(group_lines(lines)) == [["a\n"], ["b\n"], ["c\n"]]


def test_group_lines_empty_input_yields_nothing():
    assert list(group_lines([])) == []


def test_group_lines_predicate_never_called_for_first_line():
    lines = ["a\n", "b\n", "c\n"]
    calls = []

    def group_by(block, previous_line, current_line):
        calls.append(current_line)
        return False

    list(group_lines(lines, group_by))
    assert calls == ["b\n", "c\n"]


def test_group_lines_custom_predicate_basic():
    lines = ["1\n", "  cont\n", "2\n"]

    def group_by(block, previous_line, current_line):
        return current_line.startswith("  ")

    assert list(group_lines(lines, group_by)) == [["1\n", "  cont\n"], ["2\n"]]


def test_timestamp_block_grouper_stack_trace_fixture():
    lines = read_lines("stack_trace.log")
    blocks = list(group_lines(lines, timestamp_block_grouper()))
    assert len(blocks) == 2
    assert blocks[0] == lines[:5]
    assert blocks[1] == lines[5:]


def test_timestamp_block_grouper_all_timestamped_fixture():
    lines = read_lines("timestamped_lines.log")
    blocks = list(group_lines(lines, timestamp_block_grouper()))
    assert blocks == [[line] for line in lines]


def test_timestamp_block_grouper_mixed_fixture():
    lines = read_lines("mixed_blocks.log")
    blocks = list(group_lines(lines, timestamp_block_grouper()))
    assert len(blocks) == 4
    assert blocks[0] == lines[0:1]
    assert blocks[1] == lines[1:3]
    assert blocks[2] == lines[3:4]
    assert blocks[3] == lines[4:5]


def test_timestamp_block_grouper_custom_pattern_string():
    lines = ["[LOG] a\n", "cont\n", "[LOG] b\n"]
    grouper = timestamp_block_grouper(pattern=r"^\[LOG\]")
    assert list(group_lines(lines, grouper)) == [["[LOG] a\n", "cont\n"], ["[LOG] b\n"]]


def test_timestamp_block_grouper_precompiled_pattern():
    import re

    lines = ["[LOG] a\n", "cont\n", "[LOG] b\n"]
    grouper = timestamp_block_grouper(pattern=re.compile(r"^\[LOG\]"))
    assert list(group_lines(lines, grouper)) == [["[LOG] a\n", "cont\n"], ["[LOG] b\n"]]


def test_timestamp_block_grouper_unbound_falls_back_to_default_pattern():
    lines = ["2026-08-01 10:00:00 a\n", "cont\n", "2026-08-01 10:00:01 b\n"]
    assert list(group_lines(lines, timestamp_block_grouper())) == [lines[0:2], lines[2:3]]


def test_timestamp_block_grouper_bind_uses_timestamp_format():
    lines = ["01/Aug/2026:10:00:00 a\n", "cont\n", "01/Aug/2026:10:00:01 b\n"]
    grouper = timestamp_block_grouper().bind("%d/%b/%Y:%H:%M:%S")
    assert list(group_lines(lines, grouper)) == [lines[0:2], lines[2:3]]


def test_timestamp_block_grouper_bind_keeps_explicit_pattern():
    grouper = timestamp_block_grouper(pattern=r"^\[LOG\]")
    assert grouper.bind("%Y-%m-%d %H:%M:%S") is grouper


def test_timestamp_block_grouper_matches_timestamp_anywhere_in_line():
    lines = ["INFO [2026-08-01 10:00:00] a\n", "cont\n", "INFO [2026-08-01 10:00:01] b\n"]
    grouper = timestamp_block_grouper().bind("%Y-%m-%d %H:%M:%S")
    assert list(group_lines(lines, grouper)) == [lines[0:2], lines[2:3]]


def test_indent_continuation_grouper_fixture():
    lines = read_lines("indent_continuation.log")
    blocks = list(group_lines(lines, indent_continuation_grouper()))
    assert blocks == [
        lines[0:3],
        lines[3:4],
        lines[4:6],
    ]


def test_indent_continuation_grouper_tab_indentation():
    lines = ["a\n", "\tb\n"]
    assert list(group_lines(lines, indent_continuation_grouper())) == [["a\n", "\tb\n"]]
