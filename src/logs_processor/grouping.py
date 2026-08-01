from __future__ import annotations

import re
from typing import Callable, Iterable, Iterator

from .timestamps import timestamp_pattern

GroupPredicate = Callable[[list[str], str, str], bool]
"""should_continue(current_block, previous_line, current_line) -> bool.

True means current_line is a continuation of current_block; False means
current_line starts a new block.
"""

DEFAULT_TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
"""Fallback pattern for a timestamp grouping used outside a LogProcessor,
which has no timestamp_format to derive one from."""

_DEFAULT_TIMESTAMP_PATTERN = re.compile(DEFAULT_TIMESTAMP_PATTERN)


def group_lines(
    lines: Iterable[str],
    group_by: GroupPredicate | None = None,
) -> Iterator[list[str]]:
    """Group consecutive lines into blocks using group_by.

    group_by is optional. If omitted, every line becomes its own
    single-line block. group_by is never called for the first line of
    the input, since there is no previous block yet.
    """
    if group_by is None:
        def group_by(current_block: list[str], previous_line: str, current_line: str) -> bool:
            return False

    iterator = iter(lines)
    try:
        first_line = next(iterator)
    except StopIteration:
        return

    current_block = [first_line]
    previous_line = first_line

    for line in iterator:
        if group_by(current_block, previous_line, line):
            current_block.append(line)
        else:
            yield current_block
            current_block = [line]
        previous_line = line

    yield current_block


class TimestampGrouping:
    """A line carrying a timestamp starts a new block; a line without one
    is a continuation of the current block (e.g. stack trace lines).

    Build one with timestamp_block_grouper(). Left without a pattern it
    is *unbound*: LogProcessor binds it to the pipeline's
    timestamp_format -- configured or auto-detected -- when process()
    runs, so a log's timestamp is described once and serves both grouping
    and time ranges. An unbound grouping used on its own, outside a
    LogProcessor, has no format to bind to and falls back to
    DEFAULT_TIMESTAMP_PATTERN.
    """

    def __init__(self, pattern: str | re.Pattern | None = None) -> None:
        self.pattern = re.compile(pattern) if isinstance(pattern, str) else pattern

    def bind(self, timestamp_format: str) -> "TimestampGrouping":
        """Return a grouping that spots timestamp_format timestamps, or
        self if an explicit pattern was given -- an explicit pattern
        always wins over the pipeline's format."""
        if self.pattern is not None:
            return self
        return TimestampGrouping(timestamp_pattern(timestamp_format))

    def __call__(self, current_block: list[str], previous_line: str, current_line: str) -> bool:
        pattern = _DEFAULT_TIMESTAMP_PATTERN if self.pattern is None else self.pattern
        return pattern.search(current_line) is None


def timestamp_block_grouper(pattern: str | re.Pattern | None = None) -> TimestampGrouping:
    """Group lines into one block per timestamped entry.

    With no pattern the timestamps are the ones described by the
    LogProcessor's timestamp_format (auto-detected from the log's first
    lines if it isn't set). Pass a pattern to override that with a regex
    of your own -- a line it matches anywhere starts a new block.
    """
    return TimestampGrouping(pattern)


def indent_continuation_grouper() -> GroupPredicate:
    """A line whose first character is a space or tab continues the
    previous block. A blank line's first character is a newline (or the
    line is empty), so blank lines start their own block rather than
    continuing the previous one.
    """
    def should_continue(current_block: list[str], previous_line: str, current_line: str) -> bool:
        return current_line[:1] in (" ", "\t")

    return should_continue
