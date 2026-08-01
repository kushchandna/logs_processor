from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Callable, Iterable, Sequence

TimestampExtractor = Callable[[str], datetime | None]
"""extract(line) -> the datetime found in line, or None if there is none."""

TimeBound = datetime | date | str | None
"""One end of a time range: a datetime, a date (midnight that day), a
string in the pipeline's timestamp_format, or None for unbounded."""

_DIRECTIVE_PATTERNS = {
    "%Y": r"\d{4}",
    "%y": r"\d{2}",
    "%m": r"\d{1,2}",
    "%d": r"\d{1,2}",
    "%H": r"\d{1,2}",
    "%I": r"\d{1,2}",
    "%M": r"\d{1,2}",
    "%S": r"\d{1,2}",
    "%f": r"\d{1,6}",
    "%j": r"\d{1,3}",
    "%p": r"[APap]\.?[Mm]\.?",
    "%a": r"[A-Za-z]{3}",
    "%A": r"[A-Za-z]+",
    "%b": r"[A-Za-z]{3}",
    "%B": r"[A-Za-z]+",
    "%z": r"(?:[+-]\d{2}:?\d{2}(?::\d{2})?|Z)",
    "%Z": r"[A-Za-z]{1,5}",
    "%%": r"%",
}

KNOWN_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S,%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%b/%Y:%H:%M:%S %z",
    "%b %d %H:%M:%S",
    "%m-%d %H:%M:%S.%f",
    "%m-%d %H:%M:%S",
)
"""Formats detect_timestamp_format() tries, most specific first.

Deliberately excluded: time-only formats (a log whose timestamps carry no
date can't be range-filtered meaningfully) and the day/month-first
numeric formats (%d/%m/%Y vs %m/%d/%Y is a coin flip that would silently
mis-parse half the time). Set timestamp_format explicitly for those.
"""

TIMESTAMP_DETECTION_LINES = 20
"""How many lines from the head of a log detection is allowed to read."""


def timestamp_pattern(timestamp_format: str) -> re.Pattern:
    """Compile a regex that locates a timestamp_format timestamp anywhere
    in a line.

    timestamp_format is a strptime format string (e.g.
    "%Y-%m-%d %H:%M:%S"). Each directive becomes a regex for the text it
    accepts; everything else is matched literally, except whitespace,
    which matches a run of spaces/tabs the way strptime does (syslog pads
    single-digit days: "Aug  1 10:00:00"). Directives strptime supports
    but this cannot turn into a regex raise a ValueError.
    """
    parts = []
    i = 0
    while i < len(timestamp_format):
        char = timestamp_format[i]
        if char.isspace():
            parts.append(r"[ \t]+")
            while i < len(timestamp_format) and timestamp_format[i].isspace():
                i += 1
            continue
        if char != "%":
            parts.append(re.escape(char))
            i += 1
            continue
        directive = timestamp_format[i:i + 2]
        if directive not in _DIRECTIVE_PATTERNS:
            raise ValueError(
                f"Unsupported directive {directive!r} in timestamp_format "
                f"{timestamp_format!r}; supported directives are "
                f"{sorted(_DIRECTIVE_PATTERNS)}"
            )
        parts.append(_DIRECTIVE_PATTERNS[directive])
        i += 2
    return re.compile("".join(parts))


def _parser_for(timestamp_format: str) -> Callable[[str], datetime]:
    """strptime for timestamp_format, with the year pinned to 1900 when
    the format carries none.

    That is strptime's own default for a year-less format (syslog's
    "%b %d %H:%M:%S"), but supplying it explicitly keeps CPython from
    warning about the ambiguity on every single line.
    """
    if "%Y" in timestamp_format or "%y" in timestamp_format:
        def parse(text: str) -> datetime:
            return datetime.strptime(text, timestamp_format)
    else:
        yearful_format = "%Y " + timestamp_format

        def parse(text: str) -> datetime:
            return datetime.strptime("1900 " + text, yearful_format)

    return parse


def timestamp_extractor(timestamp_format: str) -> TimestampExtractor:
    """Build a function pulling the first timestamp_format timestamp out
    of a line, returning None for lines without one (blank lines, stack
    trace frames, ...).

    The timestamp does not have to be at the start of the line. Candidate
    matches that the regex accepts but strptime rejects (e.g. month 13)
    are skipped, so a later, valid timestamp on the same line still wins.

    A format without a year (e.g. syslog's "%b %d %H:%M:%S") parses into
    year 1900, as strptime does -- fine for comparing such timestamps to
    each other, but range bounds must then use the same year-less format.
    """
    pattern = timestamp_pattern(timestamp_format)
    parse = _parser_for(timestamp_format)

    def extract(line: str) -> datetime | None:
        for match in pattern.finditer(line):
            try:
                return parse(match.group(0))
            except ValueError:
                continue
        return None

    return extract


def detect_timestamp_format(
    lines: Iterable[str],
    formats: Sequence[str] = KNOWN_TIMESTAMP_FORMATS,
) -> str | None:
    """Guess which of formats the timestamps in lines are written in,
    returning None if none of them fits.

    The winner is the format that parses the most of these lines, ties
    going to the earlier entry in formats -- so a line that a specific
    format and a looser one both accept (an ISO timestamp also contains a
    bare "%m-%d %H:%M:%S") is credited to the specific one, while a stray
    timestamp inside one line's message can't outvote the format the
    other lines actually use.
    """
    lines = list(lines)
    best: str | None = None
    best_hits = 0
    for candidate in formats:
        extract = timestamp_extractor(candidate)
        hits = sum(1 for line in lines if extract(line) is not None)
        if hits > best_hits:
            best, best_hits = candidate, hits
    return best


def parse_timestamp(value: datetime | date | str, timestamp_format: str) -> datetime:
    """Coerce a time-range bound to a datetime.

    datetime values pass through; a date becomes midnight on that day
    (YAML parses an unquoted "2026-08-01" as a date); a string is parsed
    with timestamp_format and must match it exactly.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        try:
            return _parser_for(timestamp_format)(value)
        except ValueError as exc:
            raise ValueError(
                f"Timestamp {value!r} does not match timestamp_format "
                f"{timestamp_format!r}"
            ) from exc
    raise TypeError(
        f"Expected a datetime, date or str timestamp, got {type(value).__name__}"
    )
