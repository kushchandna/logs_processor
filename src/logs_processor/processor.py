from __future__ import annotations

from itertools import chain, islice
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from .filtering import (
    BlockPredicate,
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
from .grouping import (
    GroupPredicate,
    TimestampGrouping,
    group_lines,
    indent_continuation_grouper,
    timestamp_block_grouper,
)
from .timestamps import (
    TIMESTAMP_DETECTION_LINES,
    TimeBound,
    detect_timestamp_format,
    timestamp_pattern,
)
from .transforms import BlockTransform, redact, replace_pattern, transform_blocks, truncate

_FILTER_MODES = ("all", "any")

_GROUPING_TYPES = {
    "timestamp": timestamp_block_grouper,
    "indent": indent_continuation_grouper,
}
_FILTER_TYPES = {
    "equal_to": equal_to,
    "contains": contains,
    "matches": matches,
    "starts_with": starts_with,
    "ends_with": ends_with,
}
_TRANSFORM_TYPES = {
    "redact": redact,
    "replace_pattern": replace_pattern,
    "truncate": truncate,
}


def _build_from_config_entries(
    entries: list[dict[str, Any]],
    registry: dict[str, Any],
    kind: str,
) -> list[Any]:
    built = []
    for entry in entries:
        entry = dict(entry)
        type_name = entry.pop("type", None)
        if type_name not in registry:
            raise ValueError(
                f"Unknown {kind} type {type_name!r}; expected one of {sorted(registry)}"
            )
        built.append(registry[type_name](**entry))
    return built


class LogProcessor:
    """Fluent builder over the group/filter/transform pipeline.

    add_grouping/add_filter/add_transformer/add_time_range each return
    self, so calls can be chained. process()/process_file() run the built
    pipeline lazily, preserving the never-load-the-whole-file behavior of
    group_lines/filter_blocks/transform_blocks.

    timestamp_format is a strptime format string (e.g. "%Y-%m-%d
    %H:%M:%S") describing the timestamps in the log, and is the pipeline's
    single description of them: both the time ranges and a timestamp
    grouping added without a pattern of its own work off it. Leave it
    unset and it is detected from the log's first lines when process()
    runs; if nothing in the pipeline needs a timestamp, it is never
    looked at.
    """

    def __init__(self, *, filter_mode: str = "any", timestamp_format: str | None = None) -> None:
        self._groupings: list[GroupPredicate] = []
        self._filters: list[BlockPredicate] = []
        self._transformers: list[BlockTransform] = []
        self._time_ranges: list[tuple[TimeBound, TimeBound]] = []
        self.filter_mode = filter_mode
        self.timestamp_format = timestamp_format

    @property
    def filter_mode(self) -> str:
        return self._filter_mode

    @filter_mode.setter
    def filter_mode(self, value: str) -> None:
        if value not in _FILTER_MODES:
            raise ValueError(f"filter_mode must be one of {_FILTER_MODES}, got {value!r}")
        self._filter_mode = value

    @property
    def timestamp_format(self) -> str | None:
        return self._timestamp_format

    @timestamp_format.setter
    def timestamp_format(self, value: str | None) -> None:
        if value is not None:
            timestamp_pattern(value)  # raises on directives we can't match
        self._timestamp_format = value

    @classmethod
    def from_config(cls, config_path: str | Path) -> "LogProcessor":
        """Build a LogProcessor from a YAML config file.

        Schema (all keys optional):

            filter_mode: any            # "any" (default) or "all"
            timestamp_format: '%Y-%m-%d %H:%M:%S'   # optional, else detected
            groupings:
              - type: timestamp         # uses timestamp_format
                pattern: '^\\d{4}-\\d{2}-\\d{2}'   # optional regex override
              - type: indent
            filters:
              - type: contains          # equal_to/contains/matches/starts_with/ends_with
                substring: ERROR
            time_ranges:                # inclusive, OR-combined, ANDed with filters
              - start: '2026-08-01 10:00:00'
                end: '2026-08-01 10:05:00'
              - start: '2026-08-01 18:00:00'
            transforms:
              - type: redact            # redact/replace_pattern/truncate
                exclude_ip: true

        Each grouping/filter/transform entry's keys (besides "type") are
        passed as keyword arguments to the matching built-in function, so
        the accepted fields mirror that function's parameters exactly (see
        grouping.py/filtering.py/transforms.py). A time_ranges entry takes
        only "start"/"end", either of which may be omitted for an
        open-ended range. The returned LogProcessor is a normal, fully
        mutable instance -- add_grouping/add_filter/add_transformer/
        add_time_range can still be called on it afterward.
        """
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        processor = cls(
            filter_mode=config.get("filter_mode", "any"),
            timestamp_format=config.get("timestamp_format"),
        )

        for grouping in _build_from_config_entries(
            config.get("groupings", []), _GROUPING_TYPES, "grouping"
        ):
            processor.add_grouping(grouping)
        for filter_ in _build_from_config_entries(
            config.get("filters", []), _FILTER_TYPES, "filter"
        ):
            processor.add_filter(filter_)
        for entry in config.get("time_ranges", []):
            unknown = set(entry) - {"start", "end"}
            if unknown:
                raise ValueError(
                    f"Unknown time_ranges key(s) {sorted(unknown)}; expected 'start'/'end'"
                )
            processor.add_time_range(entry.get("start"), entry.get("end"))
        for transform in _build_from_config_entries(
            config.get("transforms", []), _TRANSFORM_TYPES, "transform"
        ):
            processor.add_transformer(transform)

        return processor

    def add_grouping(self, predicate: GroupPredicate) -> "LogProcessor":
        """Add a grouping predicate. If multiple are added, a line
        continues the current block if ANY of them says to continue.
        """
        self._groupings.append(predicate)
        return self

    def add_filter(self, predicate: BlockPredicate) -> "LogProcessor":
        """Add a block filter predicate. Multiple predicates combine
        according to filter_mode ("all" = AND, "any" = OR, default "any").
        """
        self._filters.append(predicate)
        return self

    def add_time_range(self, start: TimeBound = None, end: TimeBound = None) -> "LogProcessor":
        """Keep only blocks timestamped inside this range.

        Both ends are inclusive and either may be omitted for an
        open-ended range (but not both). Bounds may be datetimes, dates,
        or strings in the pipeline's timestamp_format -- string bounds are
        parsed with the format in force when process() runs, so they can
        be added before timestamp_format is set (or while relying on
        detection), and are checked here as soon as it is known.

        Call this multiple times to allow several ranges: a block is kept
        if its timestamp falls in ANY of them. Time ranges are always
        ANDed with the filters added via add_filter, whatever filter_mode
        says -- they narrow what a filter can let through, rather than
        being one more way in. Blocks whose lines carry no readable
        timestamp are dropped.
        """
        if start is None and end is None:
            raise ValueError("add_time_range needs at least one of start/end")
        if self._timestamp_format is not None:
            # Same parsing/ordering checks process() will run, but now,
            # while the offending call is still on the stack.
            within_time_ranges([(start, end)], self._timestamp_format)

        self._time_ranges.append((start, end))
        return self

    def add_transformer(self, transform: BlockTransform) -> "LogProcessor":
        """Add a block transform. Transforms are applied in the order
        added, each one's output feeding the next.
        """
        self._transformers.append(transform)
        return self

    def _combined_group_by(self, timestamp_format: str | None) -> GroupPredicate | None:
        if not self._groupings:
            return None
        groupings = tuple(
            g.bind(timestamp_format) if isinstance(g, TimestampGrouping) and timestamp_format
            else g
            for g in self._groupings
        )

        def combined(current_block: list[str], previous_line: str, current_line: str) -> bool:
            return any(g(current_block, previous_line, current_line) for g in groupings)

        return combined

    def _combined_filter(self, timestamp_format: str | None) -> BlockPredicate | None:
        predicates: list[BlockPredicate] = []
        if self._filters:
            combinator = all_of if self.filter_mode == "all" else any_of
            predicates.append(combinator(*self._filters))
        if self._time_ranges:
            predicates.append(within_time_ranges(self._time_ranges, timestamp_format))

        if not predicates:
            return None
        if len(predicates) == 1:
            return predicates[0]
        return all_of(*predicates)

    def _needs_timestamp_format(self) -> bool:
        """True if anything in the pipeline has to read timestamps: a
        time range, or a timestamp grouping without a pattern of its own.
        """
        return bool(self._time_ranges) or any(
            isinstance(g, TimestampGrouping) and g.pattern is None for g in self._groupings
        )

    def _resolve_timestamp_format(self, head: list[str]) -> str:
        if self._timestamp_format is not None:
            return self._timestamp_format

        detected = detect_timestamp_format(head)
        if detected is None:
            raise ValueError(
                "Could not detect the log's timestamp format from its first "
                f"{len(head)} line(s), and this pipeline needs one "
                "(it has a time range or a timestamp grouping). Set it "
                'explicitly -- LogProcessor(timestamp_format="%Y-%m-%d %H:%M:%S") '
                "or timestamp_format: in the config -- or give the timestamp "
                "grouping an explicit pattern."
            )
        return detected

    def _combined_transform(self) -> BlockTransform | None:
        if not self._transformers:
            return None
        transformers = tuple(self._transformers)

        def apply(block: list[str]) -> list[str]:
            for t in transformers:
                block = t(block)
            return block

        return apply

    def process(self, lines: Iterable[str]) -> Iterator[list[str]]:
        """Run the pipeline over lines, yielding one list of lines per
        surviving block.

        If the pipeline reads timestamps but timestamp_format was left
        unset, the first TIMESTAMP_DETECTION_LINES lines are read up
        front to detect it (and put back before grouping) -- so a log
        whose format can't be detected fails here rather than silently
        matching nothing. Everything after that stays lazy.
        """
        iterator = iter(lines)
        timestamp_format = self._timestamp_format

        if timestamp_format is None and self._needs_timestamp_format():
            head = list(islice(iterator, TIMESTAMP_DETECTION_LINES))
            timestamp_format = self._resolve_timestamp_format(head)
            iterator = chain(head, iterator)

        blocks: Iterator[list[str]] = group_lines(
            iterator, group_by=self._combined_group_by(timestamp_format)
        )

        filter_block = self._combined_filter(timestamp_format)
        if filter_block is not None:
            blocks = filter_blocks(blocks, filter_block)

        transform = self._combined_transform()
        if transform is not None:
            blocks = transform_blocks(blocks, transform)

        return blocks

    def process_file(self, input_path: str | Path, output_path: str | Path) -> None:
        """Stream input_path line-by-line through process() and write
        each surviving/transformed block's lines to output_path as
        they're produced. Never holds the whole file in memory.
        """
        with open(input_path, "r") as infile, open(output_path, "w") as outfile:
            for block in self.process(infile):
                for line in block:
                    outfile.write(line)
