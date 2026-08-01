from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Iterable, Iterator, Sequence

from .timestamps import TimeBound, parse_timestamp, timestamp_extractor

BlockPredicate = Callable[[list[str]], bool]
"""predicate(block) -> True keeps the block, False drops it."""


def filter_blocks(
    blocks: Iterable[list[str]],
    predicate: BlockPredicate,
) -> Iterator[list[str]]:
    for block in blocks:
        if predicate(block):
            yield block


def all_of(*predicates: BlockPredicate) -> BlockPredicate:
    """AND-combine predicates. Vacuously True if no predicates given."""
    def combined(block: list[str]) -> bool:
        return all(p(block) for p in predicates)
    return combined


def any_of(*predicates: BlockPredicate) -> BlockPredicate:
    """OR-combine predicates. Vacuously False if no predicates given."""
    def combined(block: list[str]) -> bool:
        return any(p(block) for p in predicates)
    return combined


def equal_to(value: str) -> BlockPredicate:
    """Match if any line in the block equals value exactly."""
    def predicate(block: list[str]) -> bool:
        return any(line == value for line in block)
    return predicate


def contains(substring: str) -> BlockPredicate:
    """Match if any line in the block contains substring."""
    def predicate(block: list[str]) -> bool:
        return any(substring in line for line in block)
    return predicate


def matches(pattern: str | re.Pattern) -> BlockPredicate:
    """Match if any line in the block matches pattern (via re.search)."""
    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern

    def predicate(block: list[str]) -> bool:
        return any(compiled.search(line) is not None for line in block)
    return predicate


def starts_with(prefix: str) -> BlockPredicate:
    """Match if any line in the block starts with prefix."""
    def predicate(block: list[str]) -> bool:
        return any(line.startswith(prefix) for line in block)
    return predicate


def ends_with(suffix: str) -> BlockPredicate:
    """Match if any line in the block ends with suffix.

    Lines read from a file normally carry a trailing "\\n" -- a suffix
    like "completed" will not match unless the caller accounts for that
    (e.g. suffix="completed\\n").
    """
    def predicate(block: list[str]) -> bool:
        return any(line.endswith(suffix) for line in block)
    return predicate


def within_time_ranges(
    ranges: Sequence[tuple[TimeBound, TimeBound]],
    timestamp_format: str,
    *,
    keep_untimestamped: bool = False,
) -> BlockPredicate:
    """Match if the block's timestamp falls inside any of ranges.

    A block's timestamp is the first one found in its lines (normally on
    the line that opened the block -- continuation lines such as stack
    trace frames carry none, so the whole block is judged by its header).

    Each range is a (start, end) pair, both ends inclusive; either end
    may be None for unbounded. Bounds may be datetimes, dates, or strings
    in timestamp_format. Ranges are OR-combined: the block is kept if it
    falls in at least one of them.

    A block with no parseable timestamp anywhere is dropped, unless
    keep_untimestamped is True (useful for logs with a preamble or header
    lines that should survive regardless).
    """
    extract = timestamp_extractor(timestamp_format)
    bounds = []
    for start, end in ranges:
        start_dt = None if start is None else parse_timestamp(start, timestamp_format)
        end_dt = None if end is None else parse_timestamp(end, timestamp_format)
        if start_dt is not None and end_dt is not None and start_dt > end_dt:
            raise ValueError(f"Time range start {start_dt} is after its end {end_dt}")
        bounds.append((start_dt, end_dt))

    def in_any_range(moment: datetime) -> bool:
        return any(
            (start is None or moment >= start) and (end is None or moment <= end)
            for start, end in bounds
        )

    def predicate(block: list[str]) -> bool:
        for line in block:
            moment = extract(line)
            if moment is not None:
                return in_any_range(moment)
        return keep_untimestamped

    return predicate
